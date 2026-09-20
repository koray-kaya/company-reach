import json
from pathlib import Path

import httpx
import pytest
import respx

from company_reach.tools.lindas import LindasError, build_query, fetch_companies

URL = "https://lindas.test/query"
PAGE = json.loads((Path(__file__).parent / "fixtures/lindas_page.json").read_text())
EMPTY = {"head": PAGE["head"], "results": {"bindings": []}}


def test_query_targets_municipality_and_legal_forms():
    q = build_query("3203", limit=10, offset=20)
    assert "<https://ld.admin.ch/municipality/3203>" in q
    assert "legalforms/0106" in q and "legalforms/0107" in q
    assert "LIMIT 10" in q and "OFFSET 20" in q


@respx.mock
def test_fetch_pages_until_empty_and_handles_missing_purpose():
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(200, json=PAGE),
            httpx.Response(200, json=EMPTY),
        ]
    )
    records = fetch_companies("3203", url=URL, page_size=2)
    assert route.call_count == 2
    assert [r.uid for r in records] == [
        "CHE000000001",
        "CHE000000002",
        "CHE000000003",
    ]
    assert records[0].purpose_head == "Betrieb eines Metallbauunternehmens."
    assert records[1].purpose == "" and records[1].city == "St. Gallen"


@respx.mock
def test_zero_companies_is_an_error():
    respx.post(URL).mock(return_value=httpx.Response(200, json=EMPTY))
    with pytest.raises(LindasError, match="3203"):
        fetch_companies("3203", url=URL)


@respx.mock
def test_server_error_retries_then_raises():
    route = respx.post(URL).mock(return_value=httpx.Response(503))
    with pytest.raises(LindasError):
        fetch_companies("3203", url=URL)
    assert route.call_count == 3
