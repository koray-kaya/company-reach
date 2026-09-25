import asyncio
import json
import time

import httpx
import pytest
import respx

from company_reach.errors import LlmError
from company_reach.models import SelectionCriteria
from company_reach.settings import Settings
from company_reach.tools import llm

URL = "https://api.openai.com/v1/chat/completions"  # the fixture default

CRITERIA = {
    "must": ["makes something"],
    "must_not": ["holding"],
    "positive_signals": ["Montage"],
}


def answer(
    content: str, *, finish_reason: str = "stop", tokens: int = 100
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
            "model": "GLM-5.3-Flash",
            "usage": {"prompt_tokens": 10, "completion_tokens": tokens},
        },
    )


@respx.mock
async def test_returns_model_and_provenance(settings):
    respx.post(URL).mock(return_value=answer(json.dumps(CRITERIA)))
    result, prov = await llm.ask(
        "criteria", SelectionCriteria, settings=settings, goal="g"
    )
    assert result.must == ["makes something"]
    assert prov.prompt == "criteria" and prov.prompt_version == "1"
    # The model that actually answered, not the one we asked for: a hub may
    # route elsewhere, and the score must record what produced it.
    assert prov.model == "GLM-5.3-Flash"
    assert prov.completion_tokens == 100
    assert prov.seconds >= 0


@respx.mock
async def test_truncated_answer_is_an_error_not_a_retry(settings):
    """finish_reason='length' means the budget ran out. Retrying the same
    call with the same budget would fail the same way."""
    route = respx.post(URL).mock(
        return_value=answer('{"must": ["a', finish_reason="length")
    )
    with pytest.raises(LlmError, match="truncated"):
        await llm.ask("criteria", SelectionCriteria, settings=settings, goal="g")
    assert route.call_count == 1


@respx.mock
async def test_unparsable_answer_is_retried_once_then_raises(settings):
    route = respx.post(URL).mock(return_value=answer("not json at all"))
    with pytest.raises(LlmError):
        await llm.ask("criteria", SelectionCriteria, settings=settings, goal="g")
    assert route.call_count == 2


@respx.mock
async def test_schema_violation_carries_the_raw_answer(settings):
    respx.post(URL).mock(return_value=answer(json.dumps({"must": "not a list"})))
    with pytest.raises(LlmError, match="not a list"):
        await llm.ask("criteria", SelectionCriteria, settings=settings, goal="g")


@respx.mock
async def test_reasoning_effort_is_sent(settings):
    route = respx.post(URL).mock(return_value=answer(json.dumps(CRITERIA)))
    await llm.ask(
        "criteria",
        SelectionCriteria,
        settings=settings,
        reasoning_effort="high",
        goal="g",
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["reasoning_effort"] == "high"
    # langchain-openai 1.6 sends the newer OpenAI field name, not max_tokens.
    assert sent["max_completion_tokens"] == settings.llm_max_tokens
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["temperature"] == 0


@respx.mock
async def test_concurrency_is_capped(settings, monkeypatch):
    import asyncio

    monkeypatch.setattr(settings, "llm_concurrency", 2)
    in_flight = 0
    peak = 0

    async def slow(request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return answer(json.dumps(CRITERIA))

    respx.post(URL).mock(side_effect=slow)
    await asyncio.gather(
        *[
            llm.ask("criteria", SelectionCriteria, settings=settings, goal="g")
            for _ in range(6)
        ]
    )
    assert peak <= 2


async def test_a_silent_endpoint_times_out(settings):
    """Audit H5: the timeout never reached the request, and a silent
    endpoint held a run for 44 minutes. One second here must mean one."""

    async def never_answer(reader, writer):
        await reader.read()  # returns when the client gives up and closes
        writer.close()

    server = await asyncio.start_server(never_answer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    s = settings.model_copy(
        update={
            "llm_base_url": f"http://127.0.0.1:{port}/v1",
            "llm_timeout_seconds": 1.0,
        }
    )
    started = time.monotonic()
    async with server:
        with pytest.raises(LlmError):
            await asyncio.wait_for(
                llm.ask("criteria", SelectionCriteria, settings=s, goal="g"),
                timeout=30,
            )
    assert time.monotonic() - started < 10


@respx.mock
async def test_a_rejected_request_is_not_retried(settings):
    """A 400 says the request is wrong. Asking again the same way only
    doubles the wait."""
    route = respx.post(URL).mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad"}})
    )
    with pytest.raises(LlmError):
        await llm.ask("criteria", SelectionCriteria, settings=settings, goal="g")
    assert route.call_count == 1


@respx.mock
async def test_the_connect_timeout_stays_short(settings):
    """Final review of Phase A: a flat float timeout made connect wait as long
    as a read, so an unreachable endpoint cost two times ten minutes."""
    route = respx.post(URL).mock(return_value=answer(json.dumps(CRITERIA)))
    await llm.ask("criteria", SelectionCriteria, settings=settings, goal="g")
    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout["connect"] == 10.0
    assert timeout["read"] == settings.llm_timeout_seconds


@pytest.mark.parametrize("status", [429, 500, 503])
@respx.mock
async def test_a_busy_endpoint_is_asked_again_after_the_pause(settings, status):
    """Phase A review: a rate limit or a server error is transient, so it
    is retried once — after LLM_RETRY_PAUSE_S, since an overload rarely
    clears in the same second. Nothing pinned either half."""
    s = settings.model_copy(update={"llm_retry_pause_s": 0.2})
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(status, json={"error": {"message": "busy"}}),
            answer(json.dumps(CRITERIA)),
        ]
    )
    started = time.monotonic()
    result, _ = await llm.ask("criteria", SelectionCriteria, settings=s, goal="g")
    assert result.must == ["makes something"]
    assert route.call_count == 2
    assert time.monotonic() - started >= 0.2


@pytest.mark.parametrize("status", [429, 503])
@respx.mock
async def test_a_busy_endpoint_is_asked_twice_at_most(settings, status):
    route = respx.post(URL).mock(
        return_value=httpx.Response(status, json={"error": {"message": "busy"}})
    )
    with pytest.raises(LlmError, match="after 2 attempts"):
        await llm.ask("criteria", SelectionCriteria, settings=settings, goal="g")
    assert route.call_count == 2


@respx.mock
async def test_tracing_stays_off_when_the_env_says_on(settings, traces_sent):
    """Audit: settings.langsmith_tracing had no reader, and LangChain took
    the shell's word. LANGSMITH_TRACING=true exported for another project
    sent every prompt — page text, names, about_me — to LangSmith while
    .env said false. The setting now decides, in code, for every call."""
    # read after the export, as a run started from that shell reads them
    shell = Settings(
        _env_file=None, data_dir=settings.data_dir, profile_path=settings.profile_path
    )
    assert shell.langsmith_tracing is False
    respx.post(URL).mock(return_value=answer(json.dumps(CRITERIA)))

    await llm.ask("criteria", SelectionCriteria, settings=shell, goal="Anna Muster")

    assert traces_sent() == []


@respx.mock
async def test_the_setting_is_what_turns_tracing_on(settings, traces_sent):
    """The other half: with COMPANY_REACH_TRACING=true in .env, calls are
    traced — the stand-in receives the run."""
    on = settings.model_copy(update={"langsmith_tracing": True})
    respx.post(URL).mock(return_value=answer(json.dumps(CRITERIA)))

    await llm.ask("criteria", SelectionCriteria, settings=on, goal="g")

    assert any(request.startswith("POST /runs") for request in traces_sent())
