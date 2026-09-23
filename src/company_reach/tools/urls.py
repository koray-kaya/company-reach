"""The one way this project decides what host a URL belongs to.

`muster.ch` and `www.muster.ch` are the same company and two different
hosts, and three places already needed to know that: the blocklist, the
candidate dedupe, and now the check that asks whether an e-mail address sits
on the site it was found on. Same rule, one place.
"""

from urllib.parse import urlsplit


def registered_domain(url: str) -> str | None:
    """`https://WWW.Muster.ch/impressum` → `muster.ch`. None when the URL
    carries no host at all."""
    host = urlsplit(url).hostname
    return host.lower().removeprefix("www.") if host else None


def email_domain(email: str) -> str:
    """`Anna.Muster@WWW.Muster.ch` → `muster.ch`. Comparable with
    `registered_domain`, which is the whole point of it being here."""
    return email.rsplit("@", 1)[-1].strip().lower().removeprefix("www.")
