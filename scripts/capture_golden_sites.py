"""Capture the golden site set once: what search returned, what the three
candidates said, and the page list of the site we expect.

    uv run python scripts/capture_golden_sites.py [--force]
    uv run python scripts/capture_golden_sites.py --only CHE… CHE…
    uv run python scripts/capture_golden_sites.py --reread

`--reread` keeps the saved search results and reads the candidates again —
after a change to how pages are read, without letting search, which answers
differently every day, change the candidates a human has already labelled.

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
from company_reach.tools.search import Result

V0_ANSWERS = [Path("data/v0/batch3_sites.json"), Path("data/v0/batch4_sites.json")]
OUT = Path("data/golden/sites")


def domain(url: str) -> str:
    """`https://www.example.ch/` → `example.ch`. Answers are compared by
    domain, because v0 wrote the same site three different ways."""
    host = urlsplit(url).hostname or ""
    return host.lower().removeprefix("www.")


def v0_rows() -> list[dict]:
    """The starting answers, before a human has looked. `expected` is a list
    because one company can have two right answers (`.ch` and `.com`); an
    empty list means it has no website."""
    rows: list[dict] = []
    for path in V0_ANSWERS:
        for uid, found in json.loads(path.read_text(encoding="utf-8")).items():
            expected = [domain(found["base"])] if found else []
            rows.append({"uid": uid, "expected": expected, "note": "v0"})
    return rows


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def capture(row: dict, *, settings, fetcher: Fetcher, reread: bool) -> dict:
    uid = row["uid"]
    with connect(settings.db_path) as conn:
        record = company_by_uid(conn, uid)
    if record is None:
        raise SystemExit(f"{uid} is not in the database; run `pool` first")

    folder = OUT / uid
    if reread:
        saved = json.loads((folder / "search.json").read_text(encoding="utf-8"))
        results = [Result(**item) for item in saved]
    else:
        results = await search_results(record, settings=settings)
    candidates = choose_candidates(results)
    read = await read_candidates(candidates, fetcher=fetcher)

    write_json(folder / "search.json", [asdict(r) for r in results])
    write_json(
        folder / "candidates.json",
        [{"url": url, **asdict(pages)} for url, pages in read.items()],
    )
    if row["expected"]:
        pages = await all_page_urls(
            f"https://{row['expected'][0]}/",
            fetcher=fetcher,
            limit=settings.max_page_urls,
        )
        write_json(folder / "pages.json", pages)
    else:
        (folder / "pages.json").unlink(missing_ok=True)

    return {**row, "name": record.name}


def compare(row: dict) -> str:
    """One line per company. `!` marks a line that needs a human."""
    candidates = json.loads((OUT / row["uid"] / "candidates.json").read_text())
    found = [domain(c["url"]) for c in candidates]
    expected = row["expected"]
    if not expected:
        flag = "!" if found else " "
        verdict = f"expected none, {len(found)} candidate(s)"
    else:
        flag = " " if set(expected) & set(found) else "!"
        among = "among" if flag == " " else "NOT among"
        verdict = f"expected {' or '.join(expected)} {among} them"
    return f"{flag} {row['uid']}  {verdict}: {', '.join(found) or '-'}"


async def main(force: bool, only: list[str], reread: bool) -> None:
    """`--only` redoes some companies and keeps every other row as it is.
    Search engines throttle now and then, and one throttled company should
    not cost a full rerun. Every mode but `--force` keeps the labels a human
    wrote into `expected.jsonl`."""
    expected_file = OUT / "expected.jsonl"
    if expected_file.exists() and not (force or only or reread):
        raise SystemExit(f"{OUT} already captured; pass --force to redo it")

    if expected_file.exists() and not force:
        lines = expected_file.read_text(encoding="utf-8").splitlines()
        start = [json.loads(line) for line in lines]
    else:
        start = v0_rows()

    settings = get_settings()
    fetcher = Fetcher(settings)
    rows = []
    for row in start:
        if only and row["uid"] not in only:
            rows.append(row)
            continue
        print(f"capturing {row['uid']} …", flush=True)
        rows.append(
            await capture(row, settings=settings, fetcher=fetcher, reread=reread)
        )

    expected_file.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    print("\n".join(compare(r) for r in rows))


if __name__ == "__main__":
    args = sys.argv[1:]
    only = args[args.index("--only") + 1 :] if "--only" in args else []
    asyncio.run(main(force="--force" in args, only=only, reread="--reread" in args))
