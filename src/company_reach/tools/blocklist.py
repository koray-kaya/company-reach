"""Domains a company's own website is never on.

A hit means "skip this search result". It says nothing about the company —
a firm listed on moneyhouse.ch is not thereby a worse candidate, we just
cannot learn anything from that page that the register has not already told
us.
"""

from functools import lru_cache
from importlib.resources import files

from company_reach.tools.urls import registered_domain


@lru_cache
def load_blocklist() -> frozenset[str]:
    """Read once per process. Comments and blank lines are dropped, so the
    file can explain itself to whoever edits it next."""
    raw = files("company_reach.data").joinpath("blocklist.txt").read_text("utf-8")
    return frozenset(
        line.strip().lower()
        for line in raw.splitlines()
        if line.strip() and not line.startswith("#")
    )


def is_blocked(url: str) -> bool:
    """True for the domain itself and everything under it.

    The suffix is matched on a dot boundary, not with `endswith`: without the
    dot, `mylinkedin.com` would be blocked by the `linkedin.com` entry, and a
    company with an unlucky name would silently lose its own website.
    """
    host = registered_domain(url)
    if host is None:
        return False
    return any(
        host == blocked or host.endswith(f".{blocked}") for blocked in load_blocklist()
    )
