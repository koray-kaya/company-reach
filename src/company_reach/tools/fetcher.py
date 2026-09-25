"""Fetching pages from the open internet, carefully.

Every URL this module sees came from somewhere we do not control — a search
engine, or a link on a page we did not write. Two audit items (A5) follow
from that, and both are checks that run *before* the request:

* only `http` and `https`, because a result can carry `javascript:` or
  `data:`, and `file:` would read the disk;
* only public addresses, checked twice over. A literal address in the URL is
  judged as itself; a hostname is judged on what DNS returns, because a
  perfectly ordinary name can resolve to `169.254.169.254` or into our own
  container network, and reading the text of the URL would not catch that.

Both run again on every redirect. httpx following redirects itself checked
only the first URL, so a public page answering 302 to a private address
reached our own network (audit: SSRF on redirect).

A page is decoded here, not by httpx. httpx reads the charset from the
Content-Type header only and falls back to UTF-8, so an older Latin-1 site
that names its charset in `<meta>` lost every umlaut to U+FFFD (audit).
The order is the browser's: the header, then the page's own `<meta>`, then a
guess by charset_normalizer.

The other rule worth stating: a single page that fails is not an error. It
returns a `Page` carrying what went wrong, because the company may still be
identifiable from another page. Only a home page that cannot be reached at
all raises, and only then because "we could not look" must never be recorded
as "this company has no website".
"""

import asyncio
import codecs
import hashlib
import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from charset_normalizer import from_bytes
from protego import Protego

from company_reach.errors import FetchError
from company_reach.settings import Settings

USER_AGENT = "company-reach/0.1 (+https://github.com/koray-kaya/company-reach)"
_ALLOWED_SCHEMES = ("http", "https")
_HOME_ATTEMPTS = 2
_BACKOFF_S = (2.0, 4.0)
# Enough for http → https → www → a language path, with room to spare.
_MAX_REDIRECTS = 5
# `<meta charset="…">` and `<meta http-equiv="Content-Type" content="…;
# charset=…">` alike. The standard wants them in the first 1024 bytes; a
# little more room costs nothing.
_META_CHARSET = re.compile(
    rb"""<meta[^>]+?charset\s*=\s*["']?\s*([A-Za-z0-9._:-]+)""", re.IGNORECASE
)
_SNIFF_BYTES = 4096
# What browsers do with these labels: pages that say Latin-1 are written in
# Windows-1252, whose curly quotes and dashes Latin-1 does not have.
_AS_WINDOWS_1252 = {"iso8859-1", "ascii"}


@dataclass(frozen=True)
class Page:
    """What one URL gave us. `error` and `html` are mutually exclusive in
    practice, but both are always present so a caller never has to guess
    which shape it got.

    `unreachable` marks a request that did not complete — DNS, connection,
    TLS or timeout. That may pass on another day; a refusal of ours (scheme,
    private address, robots.txt, size) never will, and a caller deciding
    whether it "looked" needs to tell the two apart.

    `no_such_host` marks the one DNS answer that will not change tomorrow:
    the name does not exist.

    `url` is always the URL asked for; `final_url` is where redirects led,
    and None when there were none."""

    url: str
    status: int | None = None
    html: str = ""
    error: str | None = None
    unreachable: bool = False
    no_such_host: bool = False
    final_url: str | None = None


async def resolve_host(host: str) -> list[str]:
    """DNS, off the event loop. Module-level so a test can replace it — the
    guard below is the thing under test, not the resolver."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _codec(label: str | None) -> str | None:
    """The Python codec for a declared charset, or None when unknown."""
    if not label:
        return None
    try:
        name = codecs.lookup(label.strip()).name
    except LookupError:
        return None
    return "cp1252" if name in _AS_WINDOWS_1252 else name


def decode(content: bytes, header_charset: str | None) -> str:
    """A page's bytes as text: the header's charset, else the page's own
    `<meta>`, else charset_normalizer's guess, else UTF-8. A byte the
    charset cannot read becomes U+FFFD rather than an error."""
    meta = _META_CHARSET.search(content[:_SNIFF_BYTES])
    declared = meta.group(1).decode("ascii") if meta else None
    for label in (header_charset, declared):
        if codec := _codec(label):
            return content.decode(codec, errors="replace")
    guess = from_bytes(content).best()
    if guess is not None:
        return str(guess)
    return content.decode("utf-8", errors="replace")


def _cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


class Fetcher:
    """Holds what has to be remembered between requests: the robots rules and
    last request time per host, and the disk cache.

    `delay_s` is a constructor argument rather than only a setting so tests
    can turn the politeness delay off without pretending a host declared
    zero.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        refetch: bool = False,
        delay_s: float | None = None,
    ) -> None:
        self._settings = settings
        self._refetch = refetch
        self._delay_s = settings.per_host_delay_s if delay_s is None else delay_s
        self._cache_dir = settings.data_dir / "cache"
        self._robots: dict[str, Protego | None] = {}
        self._last_request: dict[str, float] = {}

    # -- cache ---------------------------------------------------------------

    def cache_path(self, url: str) -> Path:
        """Where this URL's HTML is kept. Public because the `pages` table
        indexes the cache and should record where the bytes are; the naming
        stays here rather than being recomputed by whoever writes the row."""
        return self._cache_dir / f"{_cache_key(url)}.html"

    def _cached(self, url: str) -> Page | None:
        if self._refetch:
            return None
        key = _cache_key(url)
        body = self._cache_dir / f"{key}.html"
        side = self._cache_dir / f"{key}.json"
        if not (body.is_file() and side.is_file()):
            return None
        html = body.read_text("utf-8")
        if "\ufffd" in html:
            # cached before pages were decoded by their charset: the
            # characters are already lost, and only the site has them
            return None
        meta = json.loads(side.read_text(encoding="utf-8"))
        return Page(
            url=url,
            status=meta.get("status"),
            html=html,
            final_url=meta.get("final_url"),
        )

    def _store(self, page: Page) -> None:
        """Only successes. Caching a 503 would turn a transient outage into a
        permanent verdict about a company."""
        if page.error is not None or not page.html:
            return
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        key = _cache_key(page.url)
        (self._cache_dir / f"{key}.html").write_text(page.html, encoding="utf-8")
        (self._cache_dir / f"{key}.json").write_text(
            json.dumps(
                {
                    "url": page.url,
                    "final_url": page.final_url,
                    "status": page.status,
                    "fetched_at": time.time(),
                }
            ),
            encoding="utf-8",
        )

    # -- guards --------------------------------------------------------------

    async def _refuse(self, url: str) -> Page | None:
        """A Page saying why this URL must not be fetched, or None."""
        parts = urlsplit(url)
        if parts.scheme not in _ALLOWED_SCHEMES:
            return Page(
                url=url,
                error=f"refused scheme {parts.scheme!r}: only http and https "
                "are fetched",
            )
        if not parts.hostname:
            return Page(url=url, error="refused: no host in the URL")

        # A literal address is checked as itself. Asking DNS about "127.0.0.1"
        # happens to give the right answer, but then the guard's correctness
        # would rest on how a resolver treats a number — and a guard should
        # not depend on anything it can check directly.
        try:
            literal = ipaddress.ip_address(parts.hostname)
        except ValueError:
            literal = None
        if literal is not None:
            if _is_public(parts.hostname):
                return None
            return Page(
                url=url,
                error=f"refused {parts.hostname}: private or otherwise "
                "non-public address",
            )

        try:
            addresses = await resolve_host(parts.hostname)
        except (OSError, socket.gaierror) as error:
            return Page(
                url=url,
                error=f"could not resolve {parts.hostname}: {error}",
                unreachable=True,
                no_such_host=isinstance(error, socket.gaierror)
                and error.errno == socket.EAI_NONAME,
            )
        if not addresses:
            return Page(
                url=url, error=f"could not resolve {parts.hostname}", unreachable=True
            )
        if not all(_is_public(address) for address in addresses):
            return Page(
                url=url,
                error=f"refused {parts.hostname}: resolves to an address that is "
                "not public",
            )
        return None

    # -- robots --------------------------------------------------------------

    async def _rules(self, client: httpx.AsyncClient, url: str) -> Protego | None:
        """Read once per host. A missing or unreadable robots.txt allows
        everything, which is what the standard says. The client does not
        follow redirects, so a robots.txt that redirects — possibly to an
        address we would refuse — is read as no rules at all."""
        parts = urlsplit(url)
        host = parts.netloc
        if host in self._robots:
            return self._robots[host]
        robots_url = urlunsplit((parts.scheme, host, "/robots.txt", "", ""))
        rules: Protego | None = None
        try:
            answer = await client.get(robots_url)
            if answer.status_code == 200:
                rules = Protego.parse(answer.text)
        except httpx.HTTPError:
            rules = None
        self._robots[host] = rules
        return rules

    async def _wait_turn(self, host: str, rules: Protego | None) -> None:
        declared = rules.crawl_delay(USER_AGENT) if rules else None
        delay = float(declared) if declared is not None else self._delay_s
        if delay <= 0:
            return
        last = self._last_request.get(host)
        if last is not None:
            remaining = delay - (time.monotonic() - last)
            if remaining > 0:
                await asyncio.sleep(delay)
        self._last_request[host] = time.monotonic()

    # -- fetching ------------------------------------------------------------

    async def get(self, url: str) -> Page:
        """One page. Never raises: every failure comes back inside the Page."""
        cached = self._cached(url)
        if cached is not None:
            return cached

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            page = await self._follow(client, url)

        self._store(page)
        return page

    async def _follow(self, client: httpx.AsyncClient, url: str) -> Page:
        """The URL, and up to five redirects after it. Every hop is checked
        as the first one was — scheme, address, robots.txt, the per-host
        delay — before a request is made to it."""
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            refusal = await self._refuse(current)
            if refusal is not None:
                return _landed(refusal, asked=url)
            rules = await self._rules(client, current)
            if rules is not None and not rules.can_fetch(current, USER_AGENT):
                skipped = Page(
                    url=current, error="skipped: robots.txt disallows this URL"
                )
                return _landed(skipped, asked=url)

            await self._wait_turn(urlsplit(current).netloc, rules)
            try:
                answer = await client.get(current)
            except httpx.HTTPError as error:
                failed = Page(
                    url=current,
                    error=f"{type(error).__name__}: {error}",
                    unreachable=True,
                )
                return _landed(failed, asked=url)
            if not answer.is_redirect:
                return _landed(self._page(current, answer), asked=url)
            current = urljoin(current, answer.headers["location"])

        return Page(
            url=url,
            error=f"more than {_MAX_REDIRECTS} redirects",
            final_url=current,
        )

    def _page(self, url: str, answer: httpx.Response) -> Page:
        if answer.status_code >= 400:
            return Page(
                url=url,
                status=answer.status_code,
                error=f"HTTP {answer.status_code}",
            )
        if len(answer.content) > self._settings.max_page_bytes:
            return Page(
                url=url,
                status=answer.status_code,
                error=f"too large: {len(answer.content)} bytes",
            )
        html = decode(answer.content, answer.charset_encoding)
        return Page(url=url, status=answer.status_code, html=html)

    async def get_home(self, url: str) -> Page:
        """The site's home page, retried, and an error if it stays out of
        reach. This is the one place the fetcher raises: not reaching a site
        at all is infrastructure failing, and it must never be recorded as
        the finding that a company has no website."""
        last: Page | None = None
        for attempt in range(_HOME_ATTEMPTS):
            page = await self.get(url)
            if page.error is None:
                return page
            last = page
            if attempt + 1 < _HOME_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_S[attempt])
        raise FetchError(
            f"home page unreachable after {_HOME_ATTEMPTS} attempts: {url} "
            f"({last.error if last else 'unknown'})"
        )


def _landed(page: Page, *, asked: str) -> Page:
    """The Page for the URL that was asked for, saying where it ended up."""
    moved = page.url if page.url != asked else None
    return replace(page, url=asked, final_url=moved)
