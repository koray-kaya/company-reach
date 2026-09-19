# Website reading: which stack?

Research note, 2026-09-19. Question: given a candidate website for a Swiss SME,
how do we fetch the home page, find the site's pages, let a model pick the
Impressum / Über uns / Team / Kontakt pages, turn them into clean text, and pull
out e-mail addresses, the UID and the postal address — in Docker Compose, in a
codebase Koray can read.

Evidence: official docs and PyPI/GitHub metadata as of 2026-09-19, plus a probe
script run over the 15 sites v0 had already found (`data/v0/`; sites are not
named here). The probe (~90 lines, `httpx` + `selectolax` + `trafilatura` +
`protego`) fetched `robots.txt`, the sitemap, the home page and up to four
"Impressum/Kontakt/Über uns"-looking pages per site with a 1 s delay.

## Recommendation

**Default stack: `httpx` + `selectolax` + `trafilatura` + `protego`, hand-rolled
sitemap reading, on-disk cache.** 25 packages, 80 MB in `site-packages`, no
browser, runs on `python:3.12-slim` (43 MB compressed). Optional add-on, only if
the data ever shows a real need: `playwright` as a JavaScript fallback in a
separate Compose service (image `mcr.microsoft.com/playwright/python`, 1.0 GB
compressed). Do not adopt Crawl4AI or Firecrawl.

Why: the probe shows the "many sites need JavaScript" worry is unfounded for
this population. All three Wix sites served their full text statically
(475–911 words on the home page); Wix documents that it server-side renders
pages precisely so that crawlers and SEO tools can read them
(<https://support.wix.com/en/article/using-external-seo-tools>). The only
near-empty site (22 words) is a static one-page site with almost no text, not a
JavaScript shell. Of 15 sites, the plain stack found the UID on 7 (v0 by hand:
4), a clean e-mail on 13, a Swiss postal code line on 13.

## 1. Plain stack

| Piece | Choice | Status (PyPI / GitHub, 2026-09-19) |
|---|---|---|
| HTTP | `httpx` 0.28.1, BSD-3 | last release 2024-12, repo pushed 2026-03; stable, feature-complete. <https://pypi.org/project/httpx/> |
| HTML parsing | `selectolax` 0.4.12, MIT (lexbor backend, Apache-2.0) | released 2026-09-18; README benchmark 2.4 s vs BeautifulSoup 61 s. <https://github.com/rushter/selectolax> |
| Text for the model | `trafilatura` 2.2.0, Apache-2.0 | released 2026-07-31, repo pushed 2026-09-11, 6.8k stars. `extract(html, output_format="txt"|"markdown", favor_recall=True)`, plus `html2txt()` "recall over precision". <https://trafilatura.readthedocs.io/en/latest/usage-python.html> |
| robots.txt | `protego` 0.6.2, BSD-3 (Scrapy) | released 2026-06; RFC 9309 rules, `can_fetch`, `crawl_delay`, `sitemaps`. <https://github.com/scrapy/protego> |
| Sitemap | hand-rolled: `re.findall(r"<loc>(.*?)</loc>")`, follow `<sitemapindex>` | `ultimate-sitemap-parser` 1.8.1 is **GPL-3.0** (<https://pypi.org/project/ultimate-sitemap-parser/>) — importing it would pull our public tool under GPL; not worth it for ~15 lines. |

Alternatives considered: `beautifulsoup4` 4.15.0 (MIT) works but is the slow
option and adds nothing we need; `readability-lxml` 0.9 (Apache-2.0) and
`markdownify` 1.2.3 (MIT) are alive but trafilatura already does main-text
extraction, boilerplate removal and Markdown in one call. Python's own
`urllib.robotparser` would also do (`crawl_delay` since 3.6, `site_maps()` since
3.8, <https://docs.python.org/3/library/urllib.robotparser.html>), protego is
stricter on edge cases and tiny; either is fine.

Pros: small, every step is a plain function Koray can read; each library does
one thing. Cons: no JavaScript execution (see §4); trafilatura's main-text
heuristic can drop short legal pages — in the probe an Impressum came back as 29
words while `html2txt` kept the address block. Rule for the code: run
`extract(favor_recall=True)`, and if the result is under ~80 words fall back to
`html2txt`.

Note that trafilatura also ships `sitemaps.sitemap_search()` and
`spider.focused_crawler()` (<https://trafilatura.readthedocs.io/en/latest/corefunctions.html>).
They work, but they hide the fetch behind trafilatura's own downloader, so we
would lose the cache, the user-agent and the per-host delay we control. Use
trafilatura for text only.

## 2. Crawl4AI

Apache-2.0 plus a **mandatory attribution clause**: "When using Crawl4AI, you
must include one of the following attribution methods" (badge, text or citation
in README/docs), <https://github.com/unclecode/crawl4ai#license>. Very active:
0.9.3 released 2026-08-31, 83.8k stars, pushed 2026-09-18
(<https://pypi.org/project/crawl4ai/>).

Footprint: `uv pip install --dry-run crawl4ai` resolves **95 packages** (plain
stack: 25). It hard-depends on `playwright`, `patchright`, `playwright-stealth`,
`unclecode-litellm`, `numpy`, `pillow`, `aiohttp` — a browser is not optional.
Install is `pip install crawl4ai` + `crawl4ai-setup` + `playwright install
chromium`. The Docker image `unclecode/crawl4ai:latest` is 1.65 GB compressed,
needs `--shm-size=1g` and "at least 4 GB of RAM for the container"
(<https://docs.crawl4ai.com/core/self-hosting/>).

Features that map to our task: `result.markdown.fit_markdown` (PruningContentFilter
scores nodes by text/link density, <https://docs.crawl4ai.com/core/fit-markdown/>)
and `result.links` (internal/external). Both are things trafilatura and a
15-line link collector already give us.

Verdict: overkill. It solves anti-bot evasion and LLM-driven extraction at
scale; our sites are cooperative and static, our volume is a few hundred pages
per run, and a 95-package dependency tree is the opposite of "plain, readable".

## 3. Firecrawl self-hosted

AGPL-3.0 (<https://github.com/firecrawl/firecrawl/blob/main/LICENSE>). The
self-host guide requires API, PostgreSQL, Redis, RabbitMQ, a Playwright service
and workers via `docker compose up --build`, and states that screenshots,
anti-bot handling and several routes need the proprietary "Fire-engine" and are
unavailable self-hosted (<https://docs.firecrawl.dev/contributing/self-host>).
Reject: six containers to fetch 50 pages, AGPL on a tool meant for GitHub, and
the interesting parts are not in the open version.

## 4. Playwright as an optional JavaScript fallback

Cost: `playwright` 1.63.0 (Apache-2.0, released 2026-09-15) is only 4 packages,
but the browser is the price: the official image
`mcr.microsoft.com/playwright/python:v1.63.0-noble` (Ubuntu 24.04, all three
browsers preinstalled) is **1.01 GB compressed** for amd64 versus 43 MB for
`python:3.12-slim`; run with `--ipc=host` and `--init`
(<https://playwright.dev/python/docs/docker>). In Compose that means a second
service the plain service calls only when needed.

Cheap "needs JavaScript" detector, from the probe data: after the static fetch,
`extract()` and `html2txt()` both under ~50 words **and** the HTML contains a
framework mount point (`id="__next"`, `id="app"`, `id="root"`,
`<script type="module">`, `<noscript>` telling you to enable JavaScript). Log
these hits; if fewer than one in twenty sites trigger it, never build the
fallback. In the probe, zero sites triggered it.

## 5. Extraction pitfalls

E-mail. Collect from three places: `mailto:` hrefs (strip `?subject=`), the
visible text, and the raw HTML (JSON-LD `"email"` fields). Filter:
- Wix injects `<hash>@sentry.wixpress.com` / `sentry-next.wixpress.com` into
  every page: 2 of 3 Wix sites in the probe, one with 13 noise hits. Drop any
  local part that is 32 hex characters and any host in a deny list
  (`sentry`, `wixpress`, `example.`, `schema.org`, `w3.org`).
- Retina image names look like addresses (`foto@2x.png`): drop matches ending
  in an image extension or whose local part contains `@2x`/`@3x`.
- Cloudflare Email Address Obfuscation replaces the address with
  `/cdn-cgi/l/email-protection#<hex>` and a `data-cfemail` attribute, decoded by
  a deferred `email-decode.min.js`
  (<https://developers.cloudflare.com/waf/tools/scrape-shield/email-address-obfuscation/>).
  Decode: first hex byte is the key, XOR every following byte with it
  (<https://blog.jse.li/posts/cloudflare-scrape-shield/>). None of the 15 probe
  sites used it, so implement it as a 6-line function, not a dependency.
- JavaScript-assembled addresses (`"info" + "@" + domain`) are invisible to a
  static fetch; accept the loss, record "no address on site" as a finding
  (LEARNINGS §5 already treats that as a real outcome).
- Prefer the address whose domain matches the site; mark others as
  "third-party" for the review card.

UID. Format per the Federal Statistical Office: `CHE` + 8 random digits + a
modulo-11 check digit, written `CHE-123.456.789` "for readability", optionally
followed by `HR` and/or `MWST` (<https://de.wikipedia.org/wiki/Unternehmens-Identifikationsnummer>).
Regex that matched all variants seen on the probe sites
(`CHE-123.456.789`, `CHE123456789`, `CHE 123 456 789`, `CHE-123.456.789 MWST`):

```
CHE[\s\-–.]?(\d{3})[\s.]?(\d{3})[\s.]?(\d{3})(?:\s*(MWST|TVA|IVA|VAT|HR))?
```

Normalise to `CHE123456789`, verify the check digit (weights 5,4,3,2,7,6,5,4,
sum mod 11, digit = 11 − remainder, where 11 counts as 0 and 10 is invalid;
verified against the six UIDs the probe found), then compare with the
register UID. A different valid UID on the site is the "group entity" signal
from LEARNINGS §4.

Postal address. A line matching `(CH-)?[1-9]\d{3} <Town>` inside the Impressum
text is enough for the model; the probe found one on 13 of 15 sites. Leave the
street/number parsing to the model, not to a regex.

## 6. Politeness and caching

- Identify: `User-Agent: company-reach/0.1 (+<repo URL>)`. All 15 probe sites
  answered it without blocking.
- Honour `robots.txt` (`protego.can_fetch`) and `Crawl-delay`; the probe saw
  delays of 1, 3 and 5 s declared. Default 1 s per host when none is declared.
- Timeouts: `httpx.Timeout(15.0, connect=5.0)`, `follow_redirects=True`; httpx
  defaults to 5 s otherwise (<https://www.python-httpx.org/advanced/timeouts/>).
- Caps: sitemaps of two shops in the probe listed 1,659 and 4,599 URLs. Cap the
  candidate list given to the model at ~200 URLs (drop product/blog paths
  first), read at most 8 pages per site, skip responses over 2 MB (a Wix home
  page is 0.8–1.5 MB of HTML).
- Cache: `data/cache/<sha1(url)>.html` plus a JSON sidecar (`url`, `status`,
  `fetched_at`). A rerun reads from disk; a `--refetch` flag bypasses it. Keep it
  a file, not a database.

## Sources not linked above

- PyPI JSON API, 2026-09-19: <https://pypi.org/pypi/<package>/json> for
  httpx, selectolax, beautifulsoup4, trafilatura, readability-lxml,
  markdownify, protego, ultimate-sitemap-parser, crawl4ai, playwright.
- GitHub REST API, 2026-09-19: stars, `pushed_at`, license for the repos named.
- Docker Hub / MCR manifests via `docker manifest inspect`, compressed layer sums.
- Crawl4AI README: <https://github.com/unclecode/crawl4ai>.
- Playwright Python intro: <https://playwright.dev/python/docs/intro>.
