# Search layer: finding a company's website

Date: 2026-09-19. Scope: the step that turns a register entry (name, seat,
UID, purpose) into candidate URLs for the company's own website. Scale:
10–50 companies per run, a few hundred per month, 2–3 queries each, so
roughly 500–1,500 search requests a month. Runs locally in Docker Compose.

Everything below was checked against the linked sources on the date above.
SearXNG could not be run here (no Docker on this machine), so the JSON shape
is taken from the source code, not from a live response.

## 1. SearXNG as the default engine

SearXNG is a metasearch proxy: it has no index and forwards each query to
the engines you enable, then merges the results. That is also its failure
mode (section 1.4).

### 1.1 Options that matter for programmatic use

| Option | Default | What to set and why | Source |
|---|---|---|---|
| `search.formats` | `[html]` | Add `json`. "Result formats available from web, remove format to deny access." Without `json` the API answers 403. | [settings_search](https://docs.searxng.org/admin/settings/settings_search.html), [settings.yml](https://github.com/searxng/searxng/blob/master/searx/settings.yml) |
| `search.default_lang` | `"auto"` | `de-CH` is a valid code (`sxng_locales.py` line 27). We pass `language=de-CH` per request instead, so the default does not matter. | [settings_search](https://docs.searxng.org/admin/settings/settings_search.html), [sxng_locales.py](https://github.com/searxng/searxng/blob/master/searx/sxng_locales.py) |
| `search.safe_search` | `0` | Leave at 0 (none). Per-request `safesearch=0` also works. | [settings_search](https://docs.searxng.org/admin/settings/settings_search.html) |
| `search.suspended_times` | see 1.4 | Leave defaults; know them. | [settings.yml](https://github.com/searxng/searxng/blob/master/searx/settings.yml) |
| `server.limiter` | `false` | **Keep `false`.** The limiter needs Valkey and its bot detection blocks our own scripts: non-HTML formats get their own window of **4 requests per hour per IP** (`API_WINDOW = 3600`, `API_MAX = 4`), and user agents matching `python-requests`, `Python`, `curl` are classed as bots (HTTP 429). | [limiter](https://docs.searxng.org/admin/searx.limiter.html), [botdetection](https://docs.searxng.org/src/searx.botdetection.html), [ip_limit.py](https://github.com/searxng/searxng/blob/master/searx/botdetection/ip_limit.py), [http_user_agent.py](https://github.com/searxng/searxng/blob/master/searx/botdetection/http_user_agent.py) |
| `server.public_instance` | `false` | Keep `false`. | [settings_server](https://docs.searxng.org/admin/settings/settings_server.html) |
| `server.secret_key` | `ultrasecretkey` | Set via `SEARXNG_SECRET`; the container entrypoint generates one when it creates `settings.yml` from its template. | [settings_server](https://docs.searxng.org/admin/settings/settings_server.html), [entrypoint.sh](https://github.com/searxng/searxng/blob/master/container/entrypoint.sh) |
| `outgoing.request_timeout` | `3.0` s | Global timeout towards the engines; `max_request_timeout` caps a per-request `timeout_limit`. 3 s is fine on a home line; raise to 5 if engines time out. | [settings_outgoing](https://docs.searxng.org/admin/settings/settings_outgoing.html) |
| `outgoing.proxies`, `source_ips`, `retries` | unset | Only if an engine bans the IP: round-robin proxies, or `retries` "in case of an HTTP error, using different proxy/IP". Not needed at our volume from a home/university IP. | [settings_outgoing](https://docs.searxng.org/admin/settings/settings_outgoing.html) |
| `use_default_settings` + `engines.keep_only` | – | Inherit the 3,400-line default file and load only the engines we name. Merge entries by `name` to flip `disabled`. | [settings.html](https://docs.searxng.org/admin/settings/settings.html) |
| `valkey.url` | `false` | Only the limiter and a few caches need it. With `limiter: false` the Valkey service can be dropped from the compose file. | [settings_valkey](https://docs.searxng.org/admin/settings/settings_valkey.html) |

Engine status in the current default `settings.yml` (master, checked
2026-09-19): `google` **disabled: true**, `bing` disabled: true, `qwant`,
`yahoo`, `yandex` disabled: true; `duckduckgo`, `brave`, `startpage`,
`mojeek` enabled. `disabled` means "disabled by default … the user can
manually activate it", not removed
([settings_engines](https://docs.searxng.org/admin/settings/settings_engines.html)).
For an API we do not rely on preferences, so we set `disabled: false`
explicitly for each engine we keep.

### 1.2 Minimal `settings.yml` (mount at `./searxng/settings.yml`)

```yaml
# https://docs.searxng.org/admin/settings/settings.html
use_default_settings:
  engines:
    keep_only:            # general web engines only; everything else is dropped
      - google
      - duckduckgo
      - brave
      - startpage
      - mojeek
      - bing

general:
  instance_name: "company-reach search"
  enable_metrics: false   # https://docs.searxng.org/admin/settings/settings_general.html

search:
  formats: [html, json]   # json is what we call; html keeps the UI for debugging
  safe_search: 0
  default_lang: "de-CH"

server:
  limiter: false          # private instance; the limiter would 429 our own client
  public_instance: false
  image_proxy: false
  secret_key: "change-me"  # overwritten by $SEARXNG_SECRET

outgoing:
  request_timeout: 3.0
  max_request_timeout: 10.0

engines:                  # merged by name into the defaults
  - name: google
    disabled: false
  - name: bing
    disabled: false
  - name: duckduckgo
    disabled: false
  - name: brave
    disabled: false
  - name: startpage
    disabled: false
  - name: mojeek
    disabled: false
```

### 1.3 Compose service (from the official template)

`searxng-docker` is superseded; the template now lives in the main repo at
`container/docker-compose.yml`
([installation-docker](https://docs.searxng.org/admin/installation-docker.html#compose-instancing),
[README of searxng-docker](https://github.com/searxng/searxng-docker)).
Reduced to what we need:

```yaml
services:
  searxng:
    image: docker.io/searxng/searxng:latest   # pin a date tag later, e.g. 2026.9.5-...
    restart: unless-stopped
    ports:
      - "127.0.0.1:8080:8080"                 # localhost only
    environment:
      SEARXNG_SECRET: ${SEARXNG_SECRET}
      SEARXNG_BASE_URL: http://localhost:8080/
    volumes:
      - ./searxng/:/etc/searxng/              # settings.yml lives here
      - searxng-cache:/var/cache/searxng/
volumes:
  searxng-cache:
```

The official template also runs `valkey/valkey:9-alpine`; we omit it because
the limiter is off. Check: `curl 'http://localhost:8080/search?q=test&format=json'`
returns `{"query": …, "results": [...], "unresponsive_engines": [...]}`
([webutils.get_json_response](https://github.com/searxng/searxng/blob/master/searx/webutils.py)).
Each result carries `url`, `title`, `content`, `engine`, `engines`, `score`,
`category`
([result_types/_base.py](https://github.com/searxng/searxng/blob/master/searx/result_types/_base.py)).
Request parameters: `q`, `language`, `safesearch`, `engines` (comma list),
`pageno`, `format`, GET or POST
([search API](https://docs.searxng.org/dev/search_api.html)).

### 1.4 Realistic failure mode: engines block the instance

- Google answers with a CAPTCHA redirect (`sorry.google.com`, `/sorry/index`)
  which the engine detects and raises as `SearxEngineCaptcha`
  ([google engine](https://docs.searxng.org/dev/engines/online/google.html)).
- SearXNG then **suspends that engine**, not the query. Current defaults
  (`settings.yml`, master): access denied 180 s, CAPTCHA 3,600 s, too many
  requests 180 s, Cloudflare CAPTCHA 15 days, reCAPTCHA 7 days. (The
  documentation page still shows older, longer values: 86,400 s for access
  denied and CAPTCHA.) The other engines keep answering and the suspended
  ones appear in `unresponsive_engines`.
- A July 2026 test of a fresh Docker instance from one IP used as an API:
  Google returned zero parseable results, Brave was suspended ("too many
  requests"), Startpage was suspended (CAPTCHA), **DuckDuckGo answered
  reliably**. Conclusion of the tester: "out of the box, a self-hosted
  SearXNG behaves like a DuckDuckGo proxy, not a Google API"
  ([apiserpent.com, 2026-07-22](https://apiserpent.com/blog/searxng-self-hosted-serp-api-tested)).
- Data-centre IPs get challenged far more than consumer ISP addresses; the
  advice is to keep a small set of engines that answer every time rather
  than twenty where half are suspended, and to use `outgoing.proxies` only
  as a last resort
  ([ssdnodes.com](https://www.ssdnodes.com/learn/fix-searxng-engine-captcha-errors)).
- A subtle trap: engines that share a named `network:` object share one
  suspension bucket, so one CAPTCHA can suspend all of them. Do not add
  `network:` lines to engines
  ([gist, 2026](https://gist.github.com/sztlink/a58eef3c677a08c1cd16ecfc5ecec56d)).

What this means for us: run SearXNG on the laptop's home/university IP, send
at most one query per second, treat Google as a bonus, and treat DuckDuckGo +
Mojeek + Brave (engine) as the baseline. Log `unresponsive_engines` with every
query so a run can show "Google was suspended for this batch". When the
merged result set for a company is empty or contains no `.ch`/`.com` result
outside the blocklist, fall back to the paid provider (section 3).

## 2. Alternatives and fallbacks

| Provider | Cost (2026-09) | Key / card | Reliability for our task | Terms on storing results | Fit |
|---|---|---|---|---|---|
| **SearXNG** (self-hosted) | 0 | none | Depends on upstream engines; DuckDuckGo stable, Google often blocked. Scrapes engines that forbid it (Google `robots.txt` disallows `/search`; DuckDuckGo disallows `/html`, `/lite`). | No API terms; AGPL container, our code stays separate. | Default. |
| **Brave Search API** | $5 / 1,000 requests, $5 free credit per month (~1,000). Free plan ended Feb 2026. | key + credit card required | Own index, 50 QPS, `country`, `search_lang`, `count`≤20. | ToS 2026-09-01 §3(b)(i): may not "store, cache, or create a database of Search Results … other than transient storage"; §4(d) "POWERED BY BRAVE" attribution; §3(b)(xiii) no AI training. Storage needs a separate plan. | Poor: we must store results for reproducibility (LEARNINGS 4). |
| **`ddgs`** (PyPI, ex `duckduckgo_search`) | 0 | none | v9.16.0 (2026-08-26), MIT. In-process scraper of Bing, Brave, DuckDuckGo, Google, Mojeek, Startpage, Yandex, Yahoo; raises `RatelimitException` on DuckDuckGo's soft 202 block. README: "This library is for educational purposes only." | Same legal footing as SearXNG, but in our process. | Same risks as SearXNG without the container boundary; no reason to prefer it. |
| **Tavily** | 1,000 free credits / month, no card; basic search = 1 credit; then $0.008 / credit or $30 / 4,000. | key | LLM-oriented search; `country: switzerland`, `language`, `exclude_domains` (≤150), `max_results` ≤20; returns `url`, `title`, `content`, `score`. | ToS: no clause restricting storage of output; §6.5 Tavily may retain *your input* for model training; §3.2 forbids data mining of the service itself. | Viable fallback; index quality for small Swiss sites untested. |
| **Exa** | $7 / 1,000 searches (10 results), $20 sign-up + $10 / month free credit. | key | Neural/keyword index; `category: company`, `includeDomains`/`excludeDomains`, `userLocation` ISO country. | ToS §4.2(a): may not "download … copy … store … any information … obtained from … the Services, except … as otherwise expressly permitted in these Terms or by us in writing"; §1.2(c) grants Exa a licence to your input and output. | Unclear on storage; over-featured for us. |
| **Serper** | 2,500 free queries on sign-up, no card; then prepaid packs from $50 / 50,000 ($1 / 1,000), credits expire after 6 months. | key | Real Google organic results as JSON: POST `https://google.serper.dev/search` with `q`, `gl`, `hl`, `num`; `organic[].title/link/snippet`. | ToS: no explicit clause on storing results; Google's `robots.txt` is Serper's problem, not ours. | Best paid fallback: Google quality, simplest API, free tier covers months. |
| **Google Custom Search JSON API** | 100 / day free, $5 / 1,000 | – | **Closed to new customers**; existing customers until 2027-01-01. | – | Not usable. |

Sources: SearXNG default engines and robots files: [settings.yml](https://github.com/searxng/searxng/blob/master/searx/settings.yml), [google.com/robots.txt](https://www.google.com/robots.txt), [duckduckgo.com/robots.txt](https://duckduckgo.com/robots.txt). Brave: [pricing](https://api-dashboard.search.brave.com/documentation/pricing), [product page](https://brave.com/search/api/), [terms of service](https://api-dashboard.search.brave.com/documentation/resources/terms-of-service), [free tier ended Feb 2026](https://www.implicator.ai/brave-drops-free-search-api-tier-puts-all-developers-on-metered-billing/), [openclaw issue #16629](https://github.com/openclaw/openclaw/issues/16629), [Brave community on storage plan](https://community.brave.app/t/data-w-storage-rights-tos/571395). ddgs: [PyPI](https://pypi.org/project/ddgs/), [README](https://github.com/deedy5/ddgs), [exceptions.py](https://github.com/deedy5/ddgs/blob/main/ddgs/exceptions.py), [old package notice](https://pypi.org/project/duckduckgo-search/). Tavily: [credits](https://docs.tavily.com/documentation/api-credits), [search endpoint](https://docs.tavily.com/documentation/api-reference/endpoint/search), [terms](https://www.tavily.com/terms). Exa: [pricing](https://exa.ai/pricing), [search reference](https://exa.ai/docs/reference/search), [terms (PDF)](https://exa.ai/terms). Serper: [serper.dev](https://serper.dev/), [pricing breakdown 2026-07-17](https://apiserpent.com/blog/serper-pricing-credits-explained), [terms](https://serper.dev/terms). Google: [Custom Search JSON API overview](https://developers.google.com/custom-search/v1/overview).

## 3. `SearchProvider` design

One interface, two implementations, one switch in `.env`.

```python
# search/provider.py
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    engine: str          # "searxng:duckduckgo", "serper", ...

class SearchProvider(Protocol):
    name: str
    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]: ...
```

- `SearxngProvider(base_url)`: `GET {base_url}/search` with
  `q`, `format=json`, `language=de-CH`, `safesearch=0`, `engines=…`
  (comma list from config). Map `results[i]` → `SearchResult(url, title,
  content, f"searxng:{engine}")`. Keep `unresponsive_engines` in the log
  line. No key.
- `SerperProvider(api_key)`: `POST https://google.serper.dev/search`, header
  `X-API-KEY`, body `{"q": query, "gl": "ch", "hl": "de", "num": limit}`;
  map `organic[i]` → `SearchResult(link, title, snippet, "serper")`.
- Factory: `SEARCH_PROVIDER=searxng|serper` in `.env`; `SERPER_API_KEY` only
  read when chosen ("bring your own key").
- The caller logs every `(company_uid, query, provider, results)` to
  `data/` before ranking; v0's lesson is that unrecorded queries are not
  reproducible (LEARNINGS 4).

HTTP client: **httpx** (sync `httpx.Client`, one per provider, reused).
- Timeout: `httpx.Timeout(10.0, connect=5.0)`; httpx's default is 5 s of
  inactivity and it raises `TimeoutException`
  ([timeouts](https://www.python-httpx.org/advanced/timeouts/)).
  SearXNG waits up to `max_request_timeout` for engines, so the read
  timeout must exceed that.
- Retries: `httpx.HTTPTransport(retries=2)` retries **only**
  `ConnectError`/`ConnectTimeout`
  ([transports](https://www.python-httpx.org/advanced/transports/)).
  For HTTP 429/5xx write a five-line loop (sleep 2 s, 4 s, give up) rather
  than adding tenacity; that is enough at our volume.
- Set a descriptive `User-Agent` (`company-reach/0.1`) and `Accept:
  application/json`; irrelevant while `limiter: false`, but honest.
- Between SearXNG queries sleep ~1 s; it is our IP that gets banned by the
  upstream engines, not SearXNG.
- `raise_for_status()`, then `.json()`; treat SearXNG's `{"error": …}` body
  and an empty `results` list as "no result", not as a crash.

## 4. Query strategy and blocklist

Query formulations (in order; stop early when a candidate passes the UID
or Impressum check from LEARNINGS 4):

1. `"<name without legal form>" <seat>` — exact phrase plus the municipality
   from the register. Strip `AG`, `GmbH`, `SA`, `Sàrl`, `SAGL` and
   `in Liquidation` before quoting; the trade name on the site rarely
   carries the legal form.
2. `<name without legal form> Impressum` — a Swiss site that offers goods
   or services for direct purchase online must give "klare und vollständige
   Angaben über seine Identität und seine Kontaktadresse"
   ([UWG Art. 3 Abs. 1 lit. s](https://www.fedlex.admin.ch/eli/cc/1988/223_223_223/de#art_3),
   quoted and scoped by [Steiger Legal](https://steigerlegal.ch/2012/04/01/impressumspflicht-im-e-commerce-fragen-und-antworten/));
   informational sites are not obliged, but the page is conventionally
   called *Impressum* or *Kontakt* on German-language sites anyway. This
   query lands directly on the page we need to read next.
3. `"<UID>"` (e.g. `"CHE-123.456.789"`) — cheap, rare hit, decisive when it
   hits (4 of 15 sites showed their own UID; LEARNINGS 4). Try both
   `CHE-123.456.789` and `CHE123456789`.
4. Optional narrowing when 1–3 return only directories:
   `site:.ch "<name>" <seat>`. Do not start with `site:.ch`; Swiss SMEs also
   use `.com` and `.swiss`.

Ranking hints for the code, not the model: prefer results whose registered
domain contains a token of the name; dedupe by registered domain (host
minus `www.`); drop anything on the blocklist before the model sees the
list; always allow "none of these".

Blocklist (registered domains; each verified reachable on 2026-09-19 with a
plain HTTPS request, HTTP 200/202/403):

```
# Swiss registers, directories, credit and monitoring
zefix.ch  uid.admin.ch  shab.ch  handelsregister.ch  moneyhouse.ch
local.ch  search.ch  help.ch  firmenverzeichnis-schweiz.ch  kompass.com
cylex-swiss.ch  firmen.ch  vschweiz.ch  firmenfinden.ch  unternehmensindex.ch
lokalesgewerbe.ch  firmen-liste.ch  opendi.ch  easymonitoring.ch  northdata.com
dnb.com  creditreform.ch  wlw.ch  europages.com  gmbh.ch  ibiz.ch
industrie-schweiz.ch  firmen.info  firma.ch  kmu.ch  startupticker.ch
startup.ch  b2bhint.com  crunchbase.com  zoominfo.com
# social, jobs, reviews, encyclopaedias
linkedin.com  xing.com  facebook.com  instagram.com  youtube.com
kununu.com  jobs.ch  jobscout24.ch  indeed.com  trustpilot.com  yelp.ch
wikipedia.org
```

Notes from the check: `cylex.ch` redirects to `cylex-swiss.ch`;
`monetas.ch` now redirects to `dnb.com`; `tel.search.ch` is a path under
`search.ch`; `firmenwegweiser.ch`, `gelbeseiten.ch`, `swissfirms.ch`,
`companies.ch`, `firmenabc.ch` did not resolve, so they are left out.
Keep the list in a plain text file; treat a hit as "skip", never as a
signal about the company. LinkedIn stays on the list for *website* finding;
the separate, manual LinkedIn lead from LEARNINGS 5 is a different step.

## Recommendation

SearXNG in the compose file as the default provider (`limiter: false`,
`formats: [html, json]`, `keep_only` six general engines, no Valkey),
called through the `SearchProvider` protocol above with httpx. Serper as
the single paid fallback behind the same protocol: Google-quality results,
2,500 free queries without a card, `gl=ch&hl=de`, a one-line request body,
and no clause forbidding us to keep the results. Brave is ruled out by its
no-storage clause and card requirement; Google's own API is closed.
