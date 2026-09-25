"""Wrapping what a company's website wrote, so a prompt can tell it apart
from what we wrote.

Three prompts carry page text — `pick_site`, `pick_pages` and `extract` —
and each says the text between the markers is data to describe, never
instructions to follow (`design.md:300-302`). The sentence is only worth
something if a page cannot forge the markers, and until this module existed
nothing stopped it: the text went into the template exactly as it came off
the site, so a page containing `<<<END>>>` closed the block early and
everything after it read as ours.

Two defences, because the first alone failed. Every `<<<` in the page is
broken up, until none is left: one pass of `str.replace` turned `<<<<<END>>>`
into `<< <<<END>>>`, the marker rebuilt (audit). And every block's markers
carry a random nonce, `<<<PAGE-<nonce> …>>>` … `<<<END-<nonce>>>>`, fresh for
each block, so a page cannot know the marker that closes it — not even by
copying one it saw in an earlier prompt.
"""

import secrets

_OPENER = "<<<"
# Visibly the same characters, not the same token. The page keeps its
# meaning — "Preis <<< 100" still reads as it did — and the model never sees
# a second marker.
_BROKEN = "<< <"


def as_data(text: str, *, label: str = "PAGE", url: str | None = None) -> str:
    """One delimited block of untrusted text, ready to drop into a prompt."""
    nonce = secrets.token_hex(8)
    head = f"{_OPENER}{label}-{nonce}"
    head = f"{head} url={url}>>>" if url else f"{head}>>>"
    while _OPENER in text:
        text = text.replace(_OPENER, _BROKEN)
    return f"{head}\n{text}\n{_OPENER}END-{nonce}>>>"
