"""HTML to text, and the normalisation every quote check runs through.

trafilatura is built for articles: it finds the main body and throws the
furniture away. An Impressum *is* furniture — a footer block with the
address and the UID — so on exactly the pages this project cares about it can
return two dozen words and drop the lines tier 1 and tier 2 need. Measured in
the probe: one Impressum came back as 29 words while a plain conversion kept
the address block (`research/website-reading.md:53`).

Hence the rule: extract with recall favoured, and if that is thin, fall back.
"""

import re
import unicodedata

import trafilatura

# Below this, assume the extractor discarded the part we wanted.
_THIN_WORDS = 80

_SHARP_S = str.maketrans({"ß": "ss"})


def textify(html: str) -> str:
    """Readable text, or an empty string when there is nothing to read."""
    text = trafilatura.extract(html, favor_recall=True) or ""
    if len(text.split()) < _THIN_WORDS:
        # clean=False matters more than it looks. The default strips what
        # trafilatura considers page furniture — including <div id="footer">,
        # which is where an Impressum lives. Cleaning here would re-apply the
        # very judgement that lost the address block, and the fallback would
        # quietly return nothing at all.
        fallback = trafilatura.html2txt(html, clean=False) or ""
        if len(fallback.split()) > len(text.split()):
            return fallback.strip()
    return text.strip()


def normalise(text: str) -> str:
    """Fold away the differences that are typography rather than content.

    Both sides of a quote check go through this: the text the model was
    given, and the page the quote is looked for in. Without it a
    non-breaking space in an address line, or a page written with ß where
    the model typed ss, would make a perfectly true quote look invented —
    and the code would reject a correct answer for a reason nobody could see.
    """
    folded = unicodedata.normalize("NFKC", text).translate(_SHARP_S)
    return re.sub(r"\s+", " ", folded).strip().casefold()
