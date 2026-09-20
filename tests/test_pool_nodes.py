import json
from pathlib import Path

import httpx
import respx

from company_reach.nodes.load_pool import load_pool
from company_reach.nodes.screen_pool import screen_pool
from company_reach.tools.db import connect, init_db

PAGE = json.loads((Path(__file__).parent / "fixtures/lindas_page.json").read_text())


@respx.mock
def test_load_then_screen(settings):
    init_db(settings.db_path)
    respx.post(settings.lindas_url).mock(return_value=httpx.Response(200, json=PAGE))

    assert load_pool("3203", "r1", settings=settings) == 3

    kept, dropped = screen_pool("r1", settings=settings)
    assert (kept, dropped) == (2, 1)

    with connect(settings.db_path) as conn:
        rows = {
            r["uid"]: r["screen_reason"]
            for r in conn.execute("select uid, screen_reason from companies")
        }
    assert rows["CHE000000001"] is None  # operating business
    assert rows["CHE000000002"] is None  # no purpose text: we know nothing, keep
    assert rows["CHE000000003"] == "property only"
