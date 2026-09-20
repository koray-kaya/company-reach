"""The only module that talks to a language model.

Prompts live in `prompts/` as Markdown files with a version header, so they
can be read and edited without touching code. The version travels with every
score: changing a prompt must not silently reuse answers produced by the old
one. Rendering uses string.Template rather than f-strings or .format() —
company text is data, and neither `{}` nor `{{` in it can then be mistaken
for a placeholder.
"""

import asyncio
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Literal

import httpx
from langchain_openai import ChatOpenAI
from openai import LengthFinishReasonError
from pydantic import BaseModel

from company_reach.errors import LlmError, PromptError
from company_reach.settings import Settings

Effort = Literal["low", "high", "max"]

_PROMPT_DIR = Path(__file__).resolve().parents[3] / "prompts"


@lru_cache
def load_prompt(name: str) -> tuple[str, str]:
    """Return (version, template text) for `prompts/<name>.md`."""
    path = _PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise PromptError(f"no prompt file for {name!r} at {path}")
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise PromptError(f"{path.name} has no version header")
    header, _, body = raw[4:].partition("\n---\n")
    version = ""
    for line in header.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "version":
            version = value.strip()
    if not version:
        raise PromptError(f"{path.name} header has no version")
    return version, body.strip()


def render(name: str, /, **variables: str) -> tuple[str, str]:
    """Return (version, rendered text). Missing variables are an error, not
    an empty string: a prompt silently missing its goal would score nothing
    sensible and nobody would notice.

    The `/` makes `name` positional-only, which is not decoration. Without
    it, a prompt with a `$name` placeholder cannot be rendered at all: the
    caller's `name=` lands on this parameter instead of in `variables`, and
    Python raises "got multiple values for argument 'name'". A mocked test
    never sees it, because the mock replaces this function.
    """
    version, template = load_prompt(name)
    try:
        return version, Template(template).substitute(**variables)
    except KeyError as e:
        raise PromptError(f"prompt {name!r} needs variable {e.args[0]!r}") from e


@dataclass(frozen=True)
class Provenance:
    """What produced an answer. Stored with every score, so a result can
    always be traced back to the model and prompt version that made it."""

    model: str
    prompt: str
    prompt_version: str
    reasoning_effort: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str
    seconds: float


def _truncated(prompt_name: str, budget: int, effort: Effort) -> LlmError:
    """The answer ran out of budget. Never retried: the same budget runs out
    the same way, and the message has to say what to change."""
    return LlmError(
        f"{prompt_name}: answer truncated at max_completion_tokens={budget}. "
        f"Raise llm_max_tokens or lower reasoning_effort (currently {effort})."
    )


@lru_cache
def _get_semaphore(concurrency: int) -> asyncio.Semaphore:
    """One semaphore per concurrency value, for the whole process. The school
    endpoint is a shared vLLM server: measured, ten concurrent requests made
    four of them time out without raising throughput, so the cap is a
    courtesy as well as a safeguard. Caching on the value rather than holding
    a module global means a test that changes the setting simply gets its own
    semaphore — no reset hook that exists only for tests."""
    return asyncio.Semaphore(concurrency)


def _client(
    settings: Settings, max_tokens: int, effort: Effort, http_client: httpx.AsyncClient
) -> ChatOpenAI:
    """The httpx client is passed in rather than left to the OpenAI SDK, so
    that respx can intercept it and the whole suite runs offline in under a
    second. (The long timeout could also be set with `request_timeout`; the
    client is injected for the tests.) The cost is one TCP connection per
    call, against a call that measured 41-130 seconds."""
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        temperature=0,
        max_tokens=max_tokens,
        reasoning_effort=effort,
        http_async_client=http_client,
        max_retries=0,  # retrying is this module's job, and it counts attempts
    )


async def ask[ModelT: BaseModel](
    prompt_name: str,
    output_model: type[ModelT],
    /,
    *,
    settings: Settings,
    max_tokens: int | None = None,
    reasoning_effort: Effort | None = None,
    **variables: str,
) -> tuple[ModelT, Provenance]:
    """Render a prompt, call the model, return a validated object.

    `include_raw=True` is what makes a schema violation visible: without it
    langchain returns None and the caller cannot tell a refusal from a bug.
    A truncated answer is not retried — the budget that ran out once will run
    out again — but an unparsable one is, since that is usually transient.
    """
    version, text = render(prompt_name, **variables)
    effort = reasoning_effort or settings.llm_reasoning_effort
    budget = max_tokens or settings.llm_max_tokens

    last: Exception | str | None = None
    async with _get_semaphore(settings.llm_concurrency):
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10.0)
        ) as http_client:
            chain = _client(
                settings, budget, effort, http_client
            ).with_structured_output(
                output_model, method=settings.llm_structured_method, include_raw=True
            )
            for _ in range(2):
                started = time.monotonic()
                try:
                    answer = await chain.ainvoke(text)
                except LengthFinishReasonError as e:
                    raise _truncated(prompt_name, budget, effort) from e
                except Exception as e:  # transport, rate limit, endpoint error
                    last = e
                    continue
                seconds = time.monotonic() - started
                raw = answer["raw"]
                meta = raw.response_metadata
                finish = meta.get("finish_reason", "")

                if answer["parsing_error"] is not None or answer["parsed"] is None:
                    # With json_schema the SDK raises above; with the other
                    # structured-output methods a truncated answer arrives
                    # here instead, as an unparsable one.
                    if finish == "length":
                        raise _truncated(prompt_name, budget, effort)
                    last = f"{answer['parsing_error']}: {str(raw.content)[:300]}"
                    continue

                usage = raw.usage_metadata or {}
                return answer["parsed"], Provenance(
                    model=meta.get("model_name", settings.llm_model),
                    prompt=prompt_name,
                    prompt_version=version,
                    reasoning_effort=effort,
                    prompt_tokens=usage.get("input_tokens", 0),
                    completion_tokens=usage.get("output_tokens", 0),
                    finish_reason=finish,
                    seconds=seconds,
                )

    raise LlmError(f"{prompt_name}: no usable answer after 2 attempts: {last}")
