"""The one way this project decides what site a URL or an address belongs to.

`muster.ch` and `www.muster.ch` are the same company and two different
hosts, and several places need to know that: the blocklist, the candidate
dedupe, the check that asks whether an e-mail address sits on the site it was
found on, and `forget`. Same rule, one place.

The rule is the registered domain: the name a company registers, with the
public suffix after it (`muster.ch`, `muster.co.uk`). Stripping `www.` alone
was not enough (audit): a site found at `de.muster.ch` counted the company's
own `info@muster.ch` as another domain's, and guessed `info@de.muster.ch`.

tldextract reads the public suffix list, and only the copy it ships with: the
list is never fetched, at run time or in tests (decided 2026-09-25). Its
private section is included, so a customer of a site builder
(`muster.wixsite.com`) is a site of its own rather than the builder's.
"""

from urllib.parse import urlsplit

import tldextract

# Site builders whose customers get a subdomain, and which the public suffix
# list does not carry (checked against tldextract 5.3.2's copy, 2026-09-25).
# Without them every Jimdo site would be one site, `jimdosite.com`.
_SITE_BUILDERS = (
    "jimdosite.com",
    "jimdofree.com",
    "jimdo.com",
    "webnode.ch",
    "webnode.com",
    "webnode.de",
    "webnode.fr",
    "webnode.it",
    "webnode.page",
    "business.site",
    "wordpress.com",
    "weebly.com",
    "squarespace.com",
    "site123.me",
    "godaddysites.com",
    "mystrikingly.com",
)

_EXTRACT = tldextract.TLDExtract(
    suffix_list_urls=(),  # the bundled copy only
    cache_dir=None,  # and nothing written to the home directory
    include_psl_private_domains=True,
    extra_suffixes=_SITE_BUILDERS,
)


def _apex(host: str) -> str:
    """`de.Muster.ch` → `muster.ch`. A host outside the list — an address,
    a test's `.example` — is kept whole, only `www.` taken off."""
    host = host.strip().lower().rstrip(".")
    found = _EXTRACT(host)
    if found.domain and found.suffix:
        return f"{found.domain}.{found.suffix}"
    return host.removeprefix("www.")


def registered_domain(url: str) -> str | None:
    """`https://WWW.de.Muster.ch/impressum` → `muster.ch`. None when the URL
    carries no host at all."""
    host = urlsplit(url).hostname
    return _apex(host) if host else None


def email_domain(email: str) -> str:
    """`Anna.Muster@mail.Muster.ch` → `muster.ch`. Comparable with
    `registered_domain`, which is the whole point of it being here."""
    return _apex(email.rsplit("@", 1)[-1])


def inbox_domain(url: str) -> str | None:
    """Where an `info@` may be guessed for this site, or None.

    Only on a domain of the site's own: its host is its registered domain,
    `www.` aside, and not a customer space on a site builder. On
    `de.muster.ch` the guess would be a mailbox nobody set up, and on a
    builder's host it is nobody's — or, for a builder the list lacks, the
    builder's own inbox."""
    host = urlsplit(url).hostname
    if not host:
        return None
    host = host.lower().rstrip(".").removeprefix("www.")
    found = _EXTRACT(host)
    if found.is_private or found.suffix in _SITE_BUILDERS:
        return None
    return host if host == _apex(host) else None


def address_key(email: str) -> str:
    """How two spellings of one address compare: `Info@Müller.ch` and
    `info@xn--mller-kva.ch` are the same mailbox. Case is folded, and an
    internationalised domain takes its ASCII (punycode) form — the form a
    mail client may show in a reply. A domain the codec cannot encode is
    kept as written."""
    local, at, domain = email.strip().lower().rpartition("@")
    if not at:
        return domain
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return f"{local}@{domain}"
