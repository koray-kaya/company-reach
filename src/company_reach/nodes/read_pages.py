"""Fetch the chosen pages and turn them into the text `extract` will read.

The fetching itself belongs to `Fetcher` — robots, the per-host delay, the
SSRF guard and the disk cache are all its. What this node adds is three
things: one unreadable page must not cost the other nine, every page is
bounded so that one long page cannot eat the extraction prompt, and a page
that came back as an empty JavaScript shell is recorded as such.

Pages of one site are read one after another on purpose. They share a host,
so the per-host delay would serialise them anyway; gathering them would only
make the order of `page_texts` depend on which response arrived first.
"""

import re
from typing import Any

from company_reach.settings import Settings
from company_reach.tools.db import connect, record_page
from company_reach.tools.fetcher import Fetcher
from company_reach.tools.textify import textify

# Under this many words, a page has told us nothing. Measured in the probe:
# the smallest real site came back with 22 words and was genuinely that
# small (`research/website-reading.md:109-115`).
_THIN_WORDS = 50

# The markup a framework leaves behind when it renders in the browser.
_SHELL = re.compile(
    r"""id=["'](__next|app|root)["']"""
    r"""|<script[^>]+type=["']module["']"""
    r"|<noscript[^>]*>[^<]*javascript",
    re.IGNORECASE,
)


def needs_javascript(html: str, text: str) -> bool:
    """Both halves, never one.

    Thin text alone is not evidence: the probe's smallest site is a static
    one-page site with almost nothing on it, and a browser would fetch it
    again to find the same nothing. A mount point alone is not evidence
    either — Wix ships framework markup and still serves its text, which is
    why all three Wix sites in the probe read fine.

    `textify` already returns the better of trafilatura's two extractors, so
    one word count answers the research note's "both under ~50 words".
    """
    return len(text.split()) < _THIN_WORDS and bool(_SHELL.search(html))


def _truncate(text: str, limit: int) -> str:
    """Cut at a word, so the model is never handed half a name."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    head, _, _ = cut.rpartition(" ")
    return (head or cut).rstrip()


async def read_pages(
    state: dict[str, Any], *, settings: Settings, fetcher: Fetcher | None = None
) -> dict:
    urls: list[str] = state["pages_to_read"]
    fetcher = fetcher or Fetcher(settings)

    texts: dict[str, str] = {}
    needs_js: list[str] = []
    indexed: list[tuple[str, int | None, str]] = []
    for url in urls:
        page = await fetcher.get(url)
        if page.error is not None or not page.html:
            continue
        text = textify(page.html)
        if needs_javascript(page.html, text):
            needs_js.append(url)
        if text.strip():
            bounded = _truncate(text, settings.max_chars_per_page)
            texts[url] = bounded
            indexed.append((url, page.status, bounded))

    # The `pages` table has been in the schema since M1 with nothing filling
    # it, and `graph.spec.yaml:159` gives this node no database side effect —
    # but it is the only place that holds a url, its status, its cleaned text
    # and its place in the cache at once. Recorded so the review page and a
    # later run can see what the model was shown without fetching again.
    if indexed:
        with connect(settings.db_path) as conn:
            for url, status, text in indexed:
                record_page(
                    conn,
                    url,
                    status=status,
                    text=text,
                    raw_path=str(fetcher.cache_path(url)),
                )

    return {"page_texts": texts, "needs_js": needs_js}
