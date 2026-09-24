"""The child graph, now that it has a real first node.

M3 proved the shape with a one-node stub. M4 replaces that node, and nothing
above it changes: the parent still only awaits `ainvoke` and reads
`recommendation`. These tests hold that promise to its word.
"""

import httpx
import pytest
import respx

from company_reach.errors import CompanyReachError, LlmError
from company_reach.graph import build_child
from company_reach.models import CompanyRecord, RawPerson
from company_reach.nodes import find_site as node
from company_reach.nodes.enrich_company import enrich_company
from company_reach.settings import Settings
from company_reach.tools.db import connect, init_db, upsert_companies
from company_reach.tools.fetcher import Fetcher
from company_reach.tools.llm import Provenance
from company_reach.tools.search import Result

UID = "CHE000000046"
PROVENANCE = Provenance(
    model="test-model",
    prompt="draft",
    prompt_version="2",
    reasoning_effort="low",
    prompt_tokens=1,
    completion_tokens=1,
    finish_reason="stop",
    seconds=0.1,
)
SITE = "https://muster-metallbau.ch"
IMPRESSUM = (
    "<html><body><div id='footer'><h2>Impressum</h2><p>Muster Metallbau AG<br>"
    "Beispielstrasse 1<br>8000 Musterstadt<br>CHE-000.000.046 MWST<br>"
    "Anna Muster, anna@muster-metallbau.ch</p>"
    "</div></body></html>"
)


@pytest.fixture
def seeded(settings: Settings, monkeypatch) -> Settings:
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        upsert_companies(
            conn,
            [
                CompanyRecord(
                    uid=UID,
                    name="Muster Metallbau AG",
                    legal_form="0106",
                    municipality="3203",
                    street="Beispielstrasse 1",
                    postal_code="8000",
                    city="Musterstadt",
                    purpose="Herstellung und Verkauf von Metallteilen.",
                    purpose_head="Herstellung und Verkauf von Metallteilen.",
                )
            ],
            "import",
        )

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    async def no_guesses(names, **kw):
        return []

    async def one_candidate(query, *, settings, limit=10):
        return [Result(f"{SITE}/", "Muster Metallbau AG", "Metallteile", "ddg")]

    async def ask(prompt_name, output_model, *, settings, **variables):
        """One stub for every prompt the child now reaches. Dispatching on
        the name rather than the shape keeps each answer readable, and a new
        prompt fails loudly here instead of silently returning the wrong
        schema."""
        if prompt_name == "pick_site":
            answer = dict(
                chosen_url=f"{SITE}/", quote="Muster Metallbau AG", reason="fits"
            )
        elif prompt_name == "pick_pages":
            answer = dict(urls=[f"{SITE}/impressum"])
        elif prompt_name == "extract":
            answer = dict(
                description="Baut Metallteile.",
                persons=[
                    RawPerson(name="Anna Muster", email="anna@muster-metallbau.ch")
                ],
                addresses=["Beispielstrasse 1, 8000 Musterstadt"],
            )
        elif prompt_name == "draft":
            answer = dict(
                subject="Umfrage zu meiner Masterarbeit",
                body="Da Sie Metallteile fertigen, wäre Ihre Sicht wertvoll.",
            )
            return output_model(**answer), PROVENANCE
        else:  # pragma: no cover - a prompt nobody taught this stub about
            raise AssertionError(f"no stubbed answer for {prompt_name!r}")
        return output_model(**answer), None

    monkeypatch.setattr("company_reach.tools.fetcher.resolve_host", resolve)
    monkeypatch.setattr(node, "resolving_domains", no_guesses)
    monkeypatch.setattr(node, "search", one_candidate)
    monkeypatch.setattr(node.llm, "ask", ask)
    return settings


def serve(html: str = IMPRESSUM) -> None:
    respx.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{SITE}/").mock(return_value=httpx.Response(200, html=html))
    respx.get(f"{SITE}/impressum").mock(return_value=httpx.Response(200, html=html))
    respx.get(f"{SITE}/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get(host="muster-metallbau.ch").mock(return_value=httpx.Response(404))


def child(settings: Settings):
    return build_child(settings=settings, fetcher=Fetcher(settings, delay_s=0.0))


@respx.mock
async def test_the_child_loads_the_company_and_finds_its_site(seeded: Settings):
    serve()
    out = await child(seeded).ainvoke(
        {"run_id": "r1", "uid": UID, "goal": "g", "about_me": "a"}
    )
    assert out["site"].url == f"{SITE}/"
    assert out["site"].tier == "uid"


@respx.mock
async def test_a_company_without_a_site_ends_with_skip(seeded: Settings, monkeypatch):
    async def only_directories(query, *, settings, limit=10):
        return [Result("https://www.moneyhouse.ch/de/company/muster", "x", "y", "ddg")]

    monkeypatch.setattr(node, "search", only_directories)
    out = await child(seeded).ainvoke(
        {"run_id": "r1", "uid": UID, "goal": "g", "about_me": "a"}
    )
    assert out["site"] is None
    assert out["recommendation"] == "skip"


@respx.mock
async def test_an_unknown_uid_raises(seeded: Settings):
    """A company the graph was told to enrich but the database does not hold
    is a bug in the caller, not a finding about a company."""
    with pytest.raises(CompanyReachError, match="CHE111111118"):
        await child(seeded).ainvoke(
            {"run_id": "r1", "uid": "CHE111111118", "goal": "g", "about_me": "a"}
        )


@respx.mock
async def test_the_parent_still_only_needs_a_recommendation(seeded: Settings):
    """M3's contract: the wrapper awaits ainvoke and reads recommendation.
    The child grew a real node and the contract did not move."""
    serve()
    out = await enrich_company(
        {"run_id": "r1", "uid": UID, "goal": "g", "about_me": "a"},
        child=child(seeded),
        settings=seeded,
    )
    assert set(out) == {"results"}
    assert out["results"][0].uid == UID


@respx.mock
async def test_a_child_error_still_becomes_an_error_result(
    seeded: Settings, monkeypatch
):
    async def broken(prompt_name, output_model, *, settings, **variables):
        raise LlmError("endpoint down")

    monkeypatch.setattr(node.llm, "ask", broken)
    serve()
    out = await enrich_company(
        {"run_id": "r1", "uid": UID, "goal": "g", "about_me": "a"},
        child=child(seeded),
        settings=seeded,
    )
    assert out["results"][0].error_kind == "llm"


@respx.mock
async def test_a_company_with_a_site_walks_on_to_a_checked_profile(seeded: Settings):
    """M5's promise: the yes branch no longer ends at find_site. The profile
    that arrives is the checked one — Anna Muster is on the served page, so
    she survives the verbatim check and her address is on the site domain."""
    serve()
    out = await child(seeded).ainvoke(
        {"run_id": "r1", "uid": UID, "goal": "g", "about_me": "a"}
    )
    assert out["pages_to_read"]
    assert out["page_texts"]
    assert [p.name for p in out["profile"].persons] == ["Anna Muster"]
    assert out["profile"].persons[0].email_offsite is False


@respx.mock
async def test_a_company_without_a_site_still_stops_at_find_site(
    seeded: Settings, monkeypatch
):
    """The no branch is unchanged: nothing is read, so no profile exists."""

    async def only_directories(query, *, settings, limit=10):
        return [Result("https://www.moneyhouse.ch/de/company/muster", "x", "y", "ddg")]

    monkeypatch.setattr(node, "search", only_directories)
    out = await child(seeded).ainvoke(
        {"run_id": "r1", "uid": UID, "goal": "g", "about_me": "a"}
    )
    assert out.get("profile") is None
    assert out.get("page_texts") in (None, {})


# --- M6: contact, recommendation, draft --------------------------------------


@respx.mock
async def test_a_company_walks_the_whole_child_to_a_checked_draft(
    seeded: Settings,
):
    serve()
    out = await child(seeded).ainvoke(
        {"run_id": "r1", "uid": UID, "goal": "g", "about_me": "a"}
    )
    assert (out["contact"].name, out["contact"].email_kind) == ("Anna Muster", "seen")
    assert out["recommendation"] == "send"
    assert out["draft"].body.startswith("Guten Tag Anna Muster")
    assert f"?c={UID}&l=de" in out["draft"].body


@respx.mock
async def test_a_skipped_company_is_never_drafted(seeded: Settings, monkeypatch):
    asked: list[str] = []
    stub = node.llm.ask

    async def reseller(prompt_name, output_model, *, settings, **variables):
        asked.append(prompt_name)
        answer, prov = await stub(
            prompt_name, output_model, settings=settings, **variables
        )
        if prompt_name == "extract":
            answer = answer.model_copy(update={"distributor_only": True})
        return answer, prov

    monkeypatch.setattr(node.llm, "ask", reseller)
    serve()
    out = await child(seeded).ainvoke(
        {"run_id": "r1", "uid": UID, "goal": "g", "about_me": "a"}
    )
    assert out["recommendation"] == "skip"
    assert out.get("draft") is None
    assert "draft" not in asked  # no model call spent on a row nobody reads


@respx.mock
async def test_a_failure_while_drafting_is_an_error_row(seeded: Settings, monkeypatch):
    stub = node.llm.ask

    async def draft_fails(prompt_name, output_model, *, settings, **variables):
        if prompt_name == "draft":
            raise LlmError("endpoint down while drafting")
        return await stub(prompt_name, output_model, settings=settings, **variables)

    monkeypatch.setattr(node.llm, "ask", draft_fails)
    serve()
    out = await enrich_company(
        {"run_id": "r1", "uid": UID, "goal": "g", "about_me": "a"},
        child=child(seeded),
        settings=seeded,
    )
    [result] = out["results"]
    assert (result.error_kind, result.recommendation) == ("llm", None)


@respx.mock
async def test_until_contact_stops_before_the_model_drafts(seeded: Settings):
    serve()
    out = await build_child(
        settings=seeded, fetcher=Fetcher(seeded, delay_s=0.0), until="contact"
    ).ainvoke({"run_id": "r1", "uid": UID, "goal": "g", "about_me": "a"})
    assert out["recommendation"] == "send"
    assert out.get("draft") is None
