import asyncio
import json
import time

import httpx
import pytest
import respx
from pydantic import SecretStr

from company_reach.errors import LlmError
from company_reach.models import SelectionCriteria
from company_reach.settings import Settings
from company_reach.tools import llm

URL = "https://api.openai.com/v1/chat/completions"  # the fixture default
GATEWAY_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"  # the fixture default

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


# --- choose_endpoint (company-reach#73): the school hub or the AI Gateway ----


def probe_answer(marker: str = "x") -> httpx.Response:
    return answer(json.dumps({"marker": marker}))


@respx.mock
async def test_hub_is_unchanged(settings):
    """ "hub" is today's behaviour exactly: no probe, same endpoint. Nothing
    is mocked here, so a stray request to either endpoint fails the test."""
    resolved = await llm.choose_endpoint(settings)
    assert resolved.llm_endpoint == "hub"
    assert resolved.llm_base_url == settings.llm_base_url
    assert resolved.llm_model == settings.llm_model


@respx.mock
async def test_hub_fills_model_ids(settings):
    """#75: even the unchanged "hub" branch must record the equivalence set
    a score lookup needs — a run through the hub still has to recognise a
    score the gateway made under its own id."""
    resolved = await llm.choose_endpoint(settings)
    assert resolved.llm_model_ids == (settings.llm_model, settings.llm_fallback_model)


async def test_gateway_uses_the_fallback_values(settings):
    keyed = settings.model_copy(
        update={"llm_endpoint": "gateway", "llm_fallback_api_key": SecretStr("gw-key")}
    )
    resolved = await llm.choose_endpoint(keyed)
    assert resolved.llm_endpoint == "gateway"
    assert resolved.llm_base_url == "https://ai-gateway.vercel.sh/v1"
    assert resolved.llm_api_key.get_secret_value() == "gw-key"
    assert resolved.llm_model == "zai/glm-5.3-flash"


async def test_gateway_fills_model_ids_with_the_school_id_too(settings):
    """The core of #75: after this switch `llm_model` is the gateway's id,
    so `llm_model_ids` is the only place the school's original id survives
    for a score lookup to find it by."""
    keyed = settings.model_copy(
        update={"llm_endpoint": "gateway", "llm_fallback_api_key": SecretStr("gw-key")}
    )
    resolved = await llm.choose_endpoint(keyed)
    assert resolved.llm_model_ids == (settings.llm_model, settings.llm_fallback_model)
    assert settings.llm_model in resolved.llm_model_ids


async def test_gateway_without_a_key_errors_clearly(settings):
    keyed = settings.model_copy(update={"llm_endpoint": "gateway"})
    with pytest.raises(LlmError, match="LLM_FALLBACK_API_KEY"):
        await llm.choose_endpoint(keyed)


@respx.mock
async def test_auto_picks_hub_when_it_answers(settings):
    route = respx.post(URL).mock(return_value=probe_answer())
    resolved = await llm.choose_endpoint(
        settings.model_copy(update={"llm_endpoint": "auto"})
    )
    assert resolved.llm_endpoint == "hub"
    assert resolved.llm_base_url == settings.llm_base_url
    assert route.call_count == 1


@respx.mock
async def test_auto_falls_back_to_the_gateway_when_the_hub_times_out(settings):
    respx.post(URL).mock(side_effect=httpx.ConnectTimeout("no route"))
    auto = settings.model_copy(
        update={"llm_endpoint": "auto", "llm_fallback_api_key": SecretStr("gw-key")}
    )
    resolved = await llm.choose_endpoint(auto)
    assert resolved.llm_endpoint == "gateway"
    assert resolved.llm_base_url == "https://ai-gateway.vercel.sh/v1"
    assert resolved.llm_model == "zai/glm-5.3-flash"
    # #75: the id "auto" resolved away from must still be in the set, or a
    # score the hub made before this run fell back stops counting.
    assert settings.llm_model in resolved.llm_model_ids
    assert resolved.llm_model_ids == (settings.llm_model, settings.llm_fallback_model)


@respx.mock
async def test_auto_fills_model_ids_when_it_picks_the_hub(settings):
    respx.post(URL).mock(return_value=probe_answer())
    resolved = await llm.choose_endpoint(
        settings.model_copy(update={"llm_endpoint": "auto"})
    )
    assert resolved.llm_endpoint == "hub"
    assert resolved.llm_model_ids == (settings.llm_model, settings.llm_fallback_model)


@respx.mock
async def test_auto_falls_back_to_the_gateway_on_a_server_error(settings):
    respx.post(URL).mock(return_value=httpx.Response(500))
    auto = settings.model_copy(
        update={"llm_endpoint": "auto", "llm_fallback_api_key": SecretStr("gw-key")}
    )
    resolved = await llm.choose_endpoint(auto)
    assert resolved.llm_endpoint == "gateway"


@respx.mock
async def test_auto_without_a_fallback_key_still_errors_clearly(settings):
    """A down hub and no gateway key: the run cannot start either way, and
    the message has to say what is missing, not a bare connection error."""
    respx.post(URL).mock(side_effect=httpx.ConnectTimeout("no route"))
    with pytest.raises(LlmError, match="LLM_FALLBACK_API_KEY"):
        await llm.choose_endpoint(settings.model_copy(update={"llm_endpoint": "auto"}))


@respx.mock
async def test_the_auto_probe_uses_the_short_timeout(settings):
    """Not LLM_TIMEOUT_SECONDS (600 s): a probe that waited as long as a real
    call would defeat the point of deciding once, quickly, per run."""
    route = respx.post(URL).mock(return_value=probe_answer())
    s = settings.model_copy(update={"llm_endpoint": "auto", "llm_probe_timeout_s": 5.0})
    await llm.choose_endpoint(s)
    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout["read"] == 5.0
    assert timeout["connect"] == 10.0


@respx.mock
async def test_the_gateway_sends_provider_options(settings):
    route = respx.post(GATEWAY_URL).mock(return_value=answer(json.dumps(CRITERIA)))
    keyed = settings.model_copy(
        update={
            "llm_endpoint": "gateway",
            "llm_fallback_api_key": SecretStr("gw-key"),
            "llm_fallback_providers": ["deepinfra", "togetherai"],
        }
    )
    resolved = await llm.choose_endpoint(keyed)

    await llm.ask("criteria", SelectionCriteria, settings=resolved, goal="g")

    sent = json.loads(route.calls.last.request.content)
    assert sent["providerOptions"] == {
        "gateway": {
            "only": ["deepinfra", "togetherai"],
            "disallowPromptTraining": True,
        }
    }


@respx.mock
async def test_nothing_extra_goes_to_the_hub(settings):
    route = respx.post(URL).mock(return_value=answer(json.dumps(CRITERIA)))
    await llm.ask("criteria", SelectionCriteria, settings=settings, goal="g")
    sent = json.loads(route.calls.last.request.content)
    assert "providerOptions" not in sent


@respx.mock
async def test_the_gateway_request_carries_the_fallback_key(settings):
    """Review round 1, item 4: the key `choose_endpoint` put in the settings
    copy is the one the request actually authenticates with."""
    route = respx.post(GATEWAY_URL).mock(return_value=answer(json.dumps(CRITERIA)))
    keyed = settings.model_copy(
        update={
            "llm_endpoint": "gateway",
            "llm_fallback_api_key": SecretStr("gw-secret-key"),
        }
    )
    resolved = await llm.choose_endpoint(keyed)

    await llm.ask("criteria", SelectionCriteria, settings=resolved, goal="g")

    assert route.calls.last.request.headers["authorization"] == "Bearer gw-secret-key"


@respx.mock
async def test_a_client_refuses_an_unresolved_auto_endpoint(settings):
    """Review round 1, item 1: 'auto' must never reach a client, not only be
    avoided by convention — `choose_endpoint` always resolves it to 'hub' or
    'gateway' first, and this is what stops a caller that skips that step."""
    auto = settings.model_copy(update={"llm_endpoint": "auto"})
    with pytest.raises(LlmError, match="choose_endpoint"):
        await llm.ask("criteria", SelectionCriteria, settings=auto, goal="g")


async def test_hub_prints_the_endpoint(settings, capsys):
    """Review round 1, item 3: the explicit branches print too, matching the
    auto branch's style."""
    await llm.choose_endpoint(settings)
    out = capsys.readouterr().out
    assert "hub" in out
    assert "LLM_ENDPOINT" in out


async def test_gateway_prints_the_endpoint_not_the_key(settings, capsys):
    keyed = settings.model_copy(
        update={
            "llm_endpoint": "gateway",
            "llm_fallback_api_key": SecretStr("super-secret"),
        }
    )
    await llm.choose_endpoint(keyed)
    out = capsys.readouterr().out
    assert "gateway" in out
    assert "LLM_ENDPOINT" in out
    assert "super-secret" not in out
