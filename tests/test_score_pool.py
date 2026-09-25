import json

import httpx
import respx
from typer.testing import CliRunner

from company_reach import cli
from company_reach.models import (
    CompanyRecord,
    RawScore,
    ScoreBatch,
    SelectionCriteria,
    StoredCriteria,
)
from company_reach.nodes.score_pool import score_pool
from company_reach.profile import goal_hash
from company_reach.tools import llm
from company_reach.tools.db import (
    connect,
    current_criteria_hash,
    draw_batch,
    init_db,
    store_criteria,
    upsert_companies,
)

URL = "https://api.openai.com/v1/chat/completions"

CRITERIA = StoredCriteria(
    criteria=SelectionCriteria(
        must=["makes"], must_not=["holds"], positive_signals=["Montage"]
    ),
    criteria_hash="c1",
    created_at="2026-09-25T00:00:00+00:00",
)


def seed(settings, n: int) -> list[str]:
    """n screened-in companies, uid CHE000000001 upwards."""
    init_db(settings.db_path)
    records = [
        CompanyRecord(
            uid=f"CHE{i:09d}",
            name=f"Firma {i} AG",
            legal_form="0106",
            municipality="3203",
            purpose="Betrieb einer Schreinerei.",
            purpose_head="Betrieb einer Schreinerei.",
        )
        for i in range(1, n + 1)
    ]
    with connect(settings.db_path) as conn:
        upsert_companies(conn, records, "r1")
    return [r.uid for r in records]


def reply(scores: list[dict]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"scores": scores}),
                    },
                    "finish_reason": "stop",
                }
            ],
            "model": "GLM-5.3-Flash",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        },
    )


def echo_all(request) -> httpx.Response:
    """Answer with a score for every uid the prompt mentions."""
    body = json.loads(request.content)["messages"][0]["content"]
    uids = [u for u in body.split('"uid": "') if u.startswith("CHE")]
    uids = [u[:12] for u in uids]
    return reply([{"uid": u, "score": 7, "reason": "ok"} for u in uids])


def stored(settings) -> dict[str, int]:
    with connect(settings.db_path) as conn:
        return {
            r["uid"]: r["score"] for r in conn.execute("select uid, score from scores")
        }


@respx.mock
async def test_scores_every_candidate(settings):
    seed(settings, 5)
    respx.post(URL).mock(side_effect=echo_all)
    report = await score_pool("run1", "goal", CRITERIA, settings=settings)
    assert report.scored == 5 and report.cached == 0
    assert len(stored(settings)) == 5


@respx.mock
async def test_second_run_is_all_cache(settings):
    seed(settings, 5)
    respx.post(URL).mock(side_effect=echo_all)
    route = respx.post(URL).mock(side_effect=echo_all)
    await score_pool("run1", "goal", CRITERIA, settings=settings)
    after_first = route.call_count

    report = await score_pool("run2", "goal", CRITERIA, settings=settings)
    assert report.scored == 0 and report.cached == 5
    assert route.call_count == after_first  # nothing was asked again


@respx.mock
async def test_a_different_goal_is_not_cached(settings):
    seed(settings, 5)
    respx.post(URL).mock(side_effect=echo_all)
    await score_pool("run1", "goal one", CRITERIA, settings=settings)
    report = await score_pool("run2", "goal two", CRITERIA, settings=settings)
    assert report.scored == 5


@respx.mock
async def test_limit_and_seed_decide_which_companies(settings, monkeypatch):
    seed(settings, 40)
    monkeypatch.setattr(settings, "score_batch_size", 10)
    respx.post(URL).mock(side_effect=echo_all)

    await score_pool("a", "goal", CRITERIA, settings=settings, limit=10, seed=1)
    first = set(stored(settings))
    assert len(first) == 10
    # not simply the first ten uids: the selection is shuffled
    assert first != {f"CHE{i:09d}" for i in range(1, 11)}

    with connect(settings.db_path) as conn:
        conn.execute("delete from scores")
    await score_pool("b", "goal", CRITERIA, settings=settings, limit=10, seed=1)
    assert set(stored(settings)) == first  # same seed, same ten

    with connect(settings.db_path) as conn:
        conn.execute("delete from scores")
    await score_pool("c", "goal", CRITERIA, settings=settings, limit=10, seed=2)
    assert set(stored(settings)) != first  # different seed, different ten


@respx.mock
async def test_missing_uids_are_asked_for_once(settings):
    seed(settings, 5)
    calls: list[int] = []

    def half_then_rest(request):
        body = json.loads(request.content)["messages"][0]["content"]
        uids = sorted({u[:12] for u in body.split('"uid": "') if u.startswith("CHE")})
        calls.append(len(uids))
        if len(calls) == 1:
            uids = uids[:3]  # answer only three of the five
        return reply([{"uid": u, "score": 6, "reason": "ok"} for u in uids])

    route = respx.post(URL).mock(side_effect=half_then_rest)
    report = await score_pool("run1", "goal", CRITERIA, settings=settings)
    assert route.call_count == 2
    assert calls == [5, 2]  # the second call asks only for the missing two
    assert report.scored == 5


@respx.mock
async def test_invented_uid_and_bad_score_are_dropped(settings):
    seed(settings, 2)
    respx.post(URL).mock(
        side_effect=[
            reply(
                [
                    {"uid": "CHE000000001", "score": 7, "reason": "ok"},
                    {"uid": "CHE000000002", "score": 11, "reason": "out of range"},
                    {"uid": "CHE999999999", "score": 5, "reason": "never sent"},
                ]
            ),
            reply([{"uid": "CHE000000002", "score": 4, "reason": "second try"}]),
        ]
    )
    report = await score_pool("run1", "goal", CRITERIA, settings=settings)
    rows = stored(settings)
    assert "CHE999999999" not in rows
    assert rows["CHE000000001"] == 7
    assert rows["CHE000000002"] == 4  # re-asked after the invalid score
    assert report.dropped == 2  # the 11 and the invented uid


@respx.mock
async def test_one_failing_batch_does_not_stop_the_others(settings, monkeypatch):
    seed(settings, 4)
    monkeypatch.setattr(settings, "score_batch_size", 2)

    def one_bad(request):
        """Whichever batch carries CHE000000001 fails every attempt, so the
        retry inside llm.ask cannot rescue it."""
        body = json.loads(request.content)["messages"][0]["content"]
        if "CHE000000001" in body:
            return httpx.Response(500)
        return echo_all(request)

    respx.post(URL).mock(side_effect=one_bad)
    report = await score_pool("run1", "goal", CRITERIA, settings=settings)
    assert report.failed_batches == 1
    assert report.scored == 2  # the other batch went through


# --- one set of criteria per goal (audit H10) ---------------------------------

runner = CliRunner()


def _provenance(prompt: str) -> llm.Provenance:
    return llm.Provenance(
        model="test-model",
        prompt=prompt,
        prompt_version="1",
        reasoning_effort="low",
        prompt_tokens=0,
        completion_tokens=0,
        finish_reason="stop",
        seconds=0.0,
    )


def fake_model(monkeypatch, *musts: str) -> dict[str, int]:
    """`llm.ask` without an endpoint. The criteria prompt answers with the
    next of `musts` each time it is asked; the score prompt gives every
    company it was sent a 7. Returns how often each prompt was asked."""
    calls = {"criteria": 0, "score": 0}

    async def ask(prompt_name, output_model, /, *, settings, **variables):
        calls[prompt_name] += 1
        if prompt_name == "criteria":
            answer = SelectionCriteria(
                must=[musts[calls["criteria"] - 1]],
                must_not=["holds"],
                positive_signals=[],
            )
        else:
            sent = json.loads(variables["companies"])
            answer = ScoreBatch(
                scores=[RawScore(uid=c["uid"], score=7, reason="ok") for c in sent]
            )
        return answer, _provenance(prompt_name)

    monkeypatch.setattr(llm, "ask", ask)
    return calls


def criteria_hashes(settings) -> set[str | None]:
    with connect(settings.db_path) as conn:
        return {r[0] for r in conn.execute("select criteria_hash from scores")}


def test_two_score_passes_use_one_criteria(settings, monkeypatch):
    """Audit H10: every `score` wrote fresh criteria, so two passes over one
    pool were ranked against two rule sets and the cache could not tell."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    seed(settings, 4)
    calls = fake_model(monkeypatch, "makes things", "sells software")

    for _ in range(2):
        r = runner.invoke(cli.app, ["score", "--goal", "goal", "--limit", "2"])
        assert r.exit_code == 0, r.output

    assert calls["criteria"] == 1
    assert len(stored(settings)) == 4
    hashes = criteria_hashes(settings)
    assert len(hashes) == 1 and None not in hashes
    assert "makes things" in r.output  # the second pass shows the stored rules


def test_a_new_criteria_rescores(settings, monkeypatch):
    """`--new-criteria` writes a fresh set, and every score made under the
    old one stops counting: the whole pool is scored again."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    seed(settings, 3)
    calls = fake_model(monkeypatch, "makes things", "sells software")
    runner.invoke(cli.app, ["score", "--goal", "goal"])
    before = criteria_hashes(settings)

    r = runner.invoke(cli.app, ["score", "--goal", "goal", "--new-criteria"])

    assert r.exit_code == 0, r.output
    assert calls["criteria"] == 2
    assert "3 newly scored" in r.output
    assert "sells software" in r.output
    after = criteria_hashes(settings)
    assert len(after) == 1 and after != before


def test_scores_carry_the_stored_hash(settings, monkeypatch):
    """Review of Phase F: score_pool recomputed the hash from the formatted
    text while the draw read the stored one, so a change to the formatting
    alone re-scored the pool under a hash nothing draws, and stranded it."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    seed(settings, 2)
    fake_model(monkeypatch, "makes things")
    rules = SelectionCriteria(
        must=["makes things"], must_not=["holds"], positive_signals=[]
    )
    key = goal_hash("goal")
    with connect(settings.db_path) as conn:
        store_criteria(
            conn,
            key,
            rules,
            criteria_hash="by-older-code",  # not what criteria_hash() gives now
            model="test-model",
            prompt_version="1",
            adopt_unlinked=True,
        )

    r = runner.invoke(cli.app, ["score", "--goal", "goal"])

    assert r.exit_code == 0, r.output
    assert criteria_hashes(settings) == {"by-older-code"}
    with connect(settings.db_path) as conn:
        drawn = draw_batch(
            conn,
            run_id="r1",
            batch_no=1,
            goal_hash=key,
            prompt_version=llm.load_prompt("score")[0],
            model=settings.llm_model,
            criteria_hash=current_criteria_hash(conn, key),
            min_score=7,
            limit=10,
        )
    assert len(drawn) == 2
