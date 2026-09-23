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
from company_reach.models import CompanyRecord
from company_reach.nodes import find_site as node
from company_reach.nodes.enrich_company import enrich_company
from company_reach.settings import Settings
from company_reach.tools.db import connect, init_db, upsert_companies
from company_reach.tools.fetcher import Fetcher
from company_reach.tools.search import Result

UID = "CHE000000046"
SITE = "https://muster-metallbau.ch"
IMPRESSUM = (
    "<html><body><div id='footer'><h2>Impressum</h2><p>Muster Metallbau AG<br>"
    "Beispielstrasse 1<br>8000 Musterstadt<br>CHE-000.000.046 MWST</p>"
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
        return output_model(
            chosen_url=f"{SITE}/", quote="Muster Metallbau AG", reason="fits"
        ), None

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
