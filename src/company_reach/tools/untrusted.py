"""Wrapping what a company's website wrote, so a prompt can tell it apart
from what we wrote.

Three prompts carry page text — `pick_site`, `pick_pages` and `extract` —
and each says the text between the markers is data to describe, never
instructions to follow (`design.md:300-302`). The sentence is only worth
something if a page cannot forge the markers, and until this module existed
nothing stopped it: the text went into the template exactly as it came off
the site, so a page containing `<<<END>>>` closed the block early and
everything after it read as ours.

Only the opening `<<<` is neutralised, because every marker starts with it.
A page that cannot write `<<<` cannot write `<<<END>>>` either, and one
substitution is easier to be sure about than a list of them.
"""

_OPENER = "<<<"
# Visibly the same characters, not the same token. The page keeps its
# meaning — "Preis <<< 100" still reads as it did — and the model never sees
# a second marker.
_BROKEN = "<< <"


def as_data(text: str, *, label: str = "PAGE", url: str | None = None) -> str:
    """One delimited block of untrusted text, ready to drop into a prompt."""
    head = f"{_OPENER}{label} url={url}>>>" if url else f"{_OPENER}{label}>>>"
    return f"{head}\n{text.replace(_OPENER, _BROKEN)}\n{_OPENER}END>>>"
