"""The only module that talks to a language model.

Prompts live in `src/company_reach/prompts/` as Markdown files with a
version header, so they can be read and edited without touching code. The
version travels with every score: changing a prompt must not silently reuse
answers produced by the old one. Rendering uses string.Template rather than
f-strings or .format() — company text is data, and neither `{}` nor `{{` in
it can then be mistaken for a placeholder.
"""

import asyncio
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from string import Template
from typing import Literal

import httpx
from langchain_openai import ChatOpenAI
from langsmith import tracing_context
from openai import APIStatusError, LengthFinishReasonError
from pydantic import BaseModel

from company_reach.errors import LlmError, PromptError
from company_reach.settings import Settings
from company_reach.tools.gates import gate

Effort = Literal["low", "high", "max"]

# Inside the package, so an installed copy finds them as a checkout does.
PROMPT_DIR = files("company_reach").joinpath("prompts")
# Every prompt the code loads, and so every file the directory ships:
# `doctor` checks each header before a run, and a run's manifest records
# each version. Three files no code loaded were shipped once, and a broken
# header in one of them stopped every run (audit).
PROMPTS = (
    "criteria",
    "score",
    "doctor",
    "pick_site",
    "pick_pages",
    "extract",
    "draft",
)


@lru_cache
def load_prompt(name: str) -> tuple[str, str]:
    """Return (version, template text) for `prompts/<name>.md`."""
    path = PROMPT_DIR.joinpath(f"{name}.md")
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


def _get_semaphore(concurrency: int) -> asyncio.Semaphore:
    """The model's cap for the running event loop (`tools/gates.py`). The
    school endpoint is a shared vLLM server: measured, ten concurrent
    requests made four of them time out without raising throughput, so the
    cap is a courtesy as well as a safeguard."""
    return gate("llm", concurrency)


def _timeout(settings: Settings) -> httpx.Timeout:
    """Long reads, short connects: a call measured 41-130 s, but a host that
    does not answer the handshake within ten seconds will not answer at all."""
    return httpx.Timeout(settings.llm_timeout_seconds, connect=10.0)


def tracing(settings: Settings) -> AbstractContextManager[None]:
    """LangSmith tracing as the setting says (`COMPANY_REACH_TRACING`), and
    nothing else. LangChain and LangGraph decide from the process
    environment: a `LANGSMITH_TRACING=true` exported for another project
    traced every prompt, page text and names included, while .env said
    false (audit). A tracing context is read before the environment, so
    every model call and every graph run enters this one."""
    return tracing_context(enabled=settings.langsmith_tracing)


def _gateway_extra_body(settings: Settings) -> dict | None:
    """Only when the endpoint in use is the gateway: which providers may
    serve the call, and that none of them may train on it. Nothing extra
    goes to the school endpoint, which would not understand it."""
    if settings.llm_endpoint != "gateway":
        return None
    return {
        "providerOptions": {
            "gateway": {
                "only": settings.llm_fallback_providers,
                "disallowPromptTraining": True,
            }
        }
    }


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
        # The SDK sends its own per-request timeout, and it overrides the
        # injected client's: None hung a silent endpoint for good (audit H5),
        # a plain float stretched connect to the read timeout. So the same
        # Timeout object goes to both.
        timeout=_timeout(settings),
        max_retries=0,  # retrying is this module's job, and it counts attempts
        extra_body=_gateway_extra_body(settings),
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
        async with httpx.AsyncClient(timeout=_timeout(settings)) as http_client:
            chain = _client(
                settings, budget, effort, http_client
            ).with_structured_output(
                output_model, method=settings.llm_structured_method, include_raw=True
            )
            for attempt in range(2):
                if attempt:
                    await asyncio.sleep(settings.llm_retry_pause_s)
                started = time.monotonic()
                try:
                    with tracing(settings):
                        answer = await chain.ainvoke(text)
                except LengthFinishReasonError as e:
                    raise _truncated(prompt_name, budget, effort) from e
                except APIStatusError as e:
                    # A 4xx other than a timeout or a rate limit says the
                    # request itself is wrong; the same request fails again.
                    if 400 <= e.status_code < 500 and e.status_code not in (408, 429):
                        raise LlmError(
                            f"{prompt_name}: the endpoint rejected the request: {e}"
                        ) from e
                    last = e
                    continue
                except Exception as e:  # transport, timeout, endpoint error
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


class _Probe(BaseModel):
    """What `choose_endpoint`'s "auto" probe asks for — the same shape
    `doctor` asks the hub for, echoed back, so the probe also proves the
    endpoint answers and honours a JSON schema, not just that it accepts a
    connection."""

    marker: str


_PROBE_MARKER = "COMPANY-REACH-AUTO-PROBE"


def _gateway_settings(settings: Settings) -> Settings:
    if settings.llm_fallback_api_key is None:
        raise LlmError(
            "the gateway endpoint needs LLM_FALLBACK_API_KEY, which is not set"
        )
    return settings.model_copy(
        update={
            "llm_endpoint": "gateway",
            "llm_base_url": settings.llm_fallback_base_url,
            "llm_api_key": settings.llm_fallback_api_key,
            "llm_model": settings.llm_fallback_model,
        }
    )


async def choose_endpoint(settings: Settings) -> Settings:
    """Which endpoint a run uses, decided once and reused for every call the
    run makes — never per call: a call measures 41-130 s against a 600 s
    read timeout, so trying the hub on every call would cost minutes each
    time. Returns a settings copy whose llm_base_url/llm_api_key/llm_model
    point at the endpoint chosen, and whose llm_endpoint is "hub" or
    "gateway" — "auto" never comes back out.

    "hub" and "gateway" are unchanged and chosen outright. "auto" probes the
    hub, with llm_probe_timeout_s rather than the call timeout, and falls
    back to the gateway on any failure — a timeout, a connection error, a
    server error, or an answer that does not honour the schema: `ask`
    raises `LlmError` for all of them alike, so catching it here is enough.
    """
    if settings.llm_endpoint == "hub":
        return settings
    if settings.llm_endpoint == "gateway":
        return _gateway_settings(settings)

    probe = settings.model_copy(
        update={"llm_timeout_seconds": settings.llm_probe_timeout_s}
    )
    try:
        await ask("doctor", _Probe, settings=probe, marker=_PROBE_MARKER)
    except Exception as error:
        print(
            f"endpoint: {settings.llm_base_url} did not answer "
            f"({type(error).__name__}); using the gateway for this run"
        )
        return _gateway_settings(settings)
    print(f"endpoint: {settings.llm_base_url} answered; using it for this run")
    return settings.model_copy(update={"llm_endpoint": "hub"})
