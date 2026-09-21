"""Capture the golden site set once: what search returned, what the three
candidates said, and the page list of the site we expect.

    uv run python scripts/capture_golden_sites.py [--force]

It touches the network, so it runs by hand and never from the tests. The
tests read what it wrote. Search results change from day to day, and a golden
set that moves with them measures nothing — so an existing capture is kept
unless `--force` says otherwise.

Everything it writes is real company data and lands in the gitignored
`data/golden/sites/`. The answers it starts from are v0's, found by hand in
the prototype; the comparison it prints at the end is the list of places
where they need a human decision.
"""

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from company_reach.nodes.find_site import (
    all_page_urls,
    choose_candidates,
    read_candidates,
    search_results,
)
from company_reach.settings import get_settings
from company_reach.tools.db import company_by_uid, connect
from company_reach.tools.fetcher import Fetcher

V0_ANSWERS = [Path("data/v0/batch3_sites.json"), Path("data/v0/batch4_sites.json")]
OUT = Path("data/golden/sites")


def domain(url: str) -> str:
    """`https://www.example.ch/` → `example.ch`. Answers are compared by
    domain, because v0 wrote the same site three different ways."""
    host = urlsplit(url).hostname or ""
    return host.lower().removeprefix("www.")


def v0_answers() -> dict[str, str]:
    """uid → expected domain, or "none"."""
    answers: dict[str, str] = {}
    for path in V0_ANSWERS:
        for uid, found in json.loads(path.read_text(encoding="utf-8")).items():
            answers[uid] = domain(found["base"]) if found else "none"
    return answers


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def capture(uid: str, expected: str, *, settings, fetcher: Fetcher) -> dict:
    with connect(settings.db_path) as conn:
        record = company_by_uid(conn, uid)
    if record is None:
        raise SystemExit(f"{uid} is not in the database; run `pool` first")

    results = await search_results(record, settings=settings)
    candidates = choose_candidates(results)
    read = await read_candidates(candidates, fetcher=fetcher)

    folder = OUT / uid
    write_json(folder / "search.json", [asdict(r) for r in results])
    write_json(
        folder / "candidates.json",
        [{"url": url, **asdict(pages)} for url, pages in read.items()],
    )
    if expected != "none":
        pages = await all_page_urls(
            f"https://{expected}/", fetcher=fetcher, limit=settings.max_page_urls
        )
        write_json(folder / "pages.json", pages)

    return {"uid": uid, "name": record.name, "expected": expected, "note": "v0"}


def compare(row: dict) -> str:
    """One line per company. `!` marks a line that needs a human."""
    candidates = json.loads((OUT / row["uid"] / "candidates.json").read_text())
    found = [domain(c["url"]) for c in candidates]
    expected = row["expected"]
    if expected == "none":
        flag = "!" if found else " "
        verdict = f"v0 none, {len(found)} candidate(s)"
    else:
        flag = " " if expected in found else "!"
        verdict = f"v0 {expected} {'among' if flag == ' ' else 'NOT among'} them"
    return f"{flag} {row['uid']}  {verdict}: {', '.join(found) or '-'}"


async def main(force: bool) -> None:
    expected_file = OUT / "expected.jsonl"
    if expected_file.exists() and not force:
        raise SystemExit(f"{OUT} already captured; pass --force to redo it")

    settings = get_settings()
    fetcher = Fetcher(settings)
    rows = []
    for uid, expected in v0_answers().items():
        print(f"capturing {uid} …", flush=True)
        rows.append(await capture(uid, expected, settings=settings, fetcher=fetcher))

    expected_file.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    print("\n".join(compare(r) for r in rows))


if __name__ == "__main__":
    asyncio.run(main(force="--force" in sys.argv))
