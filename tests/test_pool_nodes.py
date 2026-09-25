import copy
import json
from pathlib import Path

import httpx
import respx

from company_reach.nodes.load_pool import load_pool
from company_reach.nodes.screen_pool import screen_pool
from company_reach.tools.db import connect, init_db

PAGE = json.loads((Path(__file__).parent / "fixtures/lindas_page.json").read_text())


def reasons(settings) -> dict[str, str | None]:
    with connect(settings.db_path) as conn:
        return {
            r["uid"]: r["screen_reason"]
            for r in conn.execute("select uid, screen_reason from companies")
        }


@respx.mock
def test_load_then_screen(settings):
    init_db(settings.db_path)
    respx.post(settings.lindas_url).mock(return_value=httpx.Response(200, json=PAGE))

    assert load_pool("3203", "r1", settings=settings) == 3

    kept, dropped = screen_pool(settings=settings)
    assert (kept, dropped) == (2, 1)

    rows = reasons(settings)
    assert rows["CHE000000001"] is None  # operating business
    assert rows["CHE000000002"] is None  # no purpose text: we know nothing, keep
    assert rows["CHE000000003"] == "property only"


@respx.mock
def test_an_import_is_screened(settings):
    """Audit: screening was a separate command keyed by the import's run id.
    A mistyped or stale id screened nothing, printed "kept 0, dropped 0",
    and an unscreened company read as kept to `score` and `run`."""
    init_db(settings.db_path)
    respx.post(settings.lindas_url).mock(return_value=httpx.Response(200, json=PAGE))

    load_pool("3203", "r1", settings=settings)

    rows = reasons(settings)
    assert rows["CHE000000001"] is None
    assert rows["CHE000000003"] == "property only"


@respx.mock
def test_a_reimport_updates_the_screen(settings):
    """A company that went into liquidation since the first import is dropped
    by the next one, and one whose purpose changed is kept again."""
    init_db(settings.db_path)
    route = respx.post(settings.lindas_url).mock(
        return_value=httpx.Response(200, json=PAGE)
    )
    load_pool("3203", "r1", settings=settings)

    later = copy.deepcopy(PAGE)
    first, _, third = later["results"]["bindings"]
    first["name"]["value"] = "Muster Metallbau AG in Liquidation"
    third["desc"]["value"] = "Herstellung von Fenstern."
    route.mock(return_value=httpx.Response(200, json=later))
    load_pool("3203", "r2", settings=settings)

    rows = reasons(settings)
    assert rows["CHE000000001"] == "in liquidation"
    assert rows["CHE000000003"] is None


@respx.mock
def test_screen_re_applies_the_rules_to_every_company(settings):
    """`screen` is for rules that changed: it covers every row, whichever
    import brought it in."""
    init_db(settings.db_path)
    respx.post(settings.lindas_url).mock(return_value=httpx.Response(200, json=PAGE))
    load_pool("3203", "r1", settings=settings)
    with connect(settings.db_path) as conn:  # as if screened by older rules
        conn.execute("update companies set screen_reason = null")

    assert screen_pool(settings=settings) == (2, 1)
    assert reasons(settings)["CHE000000003"] == "property only"
