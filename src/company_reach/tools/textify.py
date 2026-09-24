"""HTML to text, and the normalisation every quote check runs through.

trafilatura is built for articles: it finds the main body and throws the
furniture away. An Impressum *is* furniture — a footer block with the
address and the UID — so on exactly the pages this project cares about it can
return two dozen words and drop the lines tier 1 and tier 2 need. Measured in
the probe: one Impressum came back as 29 words while a plain conversion kept
the address block (`research/website-reading.md:53`).

Hence the rule: extract with recall favoured, and if that is thin, fall back.

E-mail addresses get lost three ways, all measured in the M6 live run. Both
extractors keep what a link *says* and drop where it points, and a site
writes "E-Mail" or a person's name and puts the address in `mailto:`.
Cloudflare encodes addresses so the page itself says "[email protected]".
And a contact block is furniture too — an `<address>` in the header, a
"quick contact" bar — so a visible address goes with it. So the addresses
are collected from the HTML with lxml (the parser trafilatura runs on),
written in beside their link text, and any the extracted text still lacks
are added at its end. Every one of them is a string the page carries.
"""

import re
import unicodedata
from urllib.parse import unquote

import lxml.html
import trafilatura
from lxml.etree import ParserError

# Below this, assume the extractor discarded the part we wanted.
_THIN_WORDS = 80

_SHARP_S = str.maketrans({"ß": "ss"})
# Code, not text. The fallback below keeps page furniture on purpose, and
# without this it kept a Wix page's JavaScript as if it were the page.
_CODE = re.compile(r"<(script|style|noscript)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)


def _cloudflare(encoded: str) -> str | None:
    """Cloudflare's e-mail protection: hex bytes, each XORed with the first."""
    try:
        key = int(encoded[:2], 16)
        chars = [
            chr(int(encoded[i : i + 2], 16) ^ key) for i in range(2, len(encoded), 2)
        ]
    except ValueError:
        return None
    address = "".join(chars)
    return address if "@" in address else None


def reveal_addresses(html: str) -> tuple[str, list[str]]:
    """The page with every hidden e-mail address written out, and the
    addresses themselves in page order.

    `<a href="mailto:anna@x.ch">Anna Muster</a>` reads "Anna Muster
    (anna@x.ch)"; Cloudflare's encoded addresses are decoded in place. A page
    with nothing hidden, or one lxml cannot parse, comes back unchanged.
    """
    try:
        doc = lxml.html.fromstring(html)
    except (ParserError, ValueError):
        return html, []
    changed = False
    found: list[str] = []

    for element in doc.xpath("//*[@data-cfemail]"):
        address = _cloudflare(element.get("data-cfemail"))
        if address:
            for child in list(element):
                element.remove(child)
            element.text = address
            found.append(address)
            changed = True
    for link in doc.xpath("//a[contains(@href, '/cdn-cgi/l/email-protection#')]"):
        address = _cloudflare(link.get("href").split("#", 1)[1])
        if address:
            link.set("href", f"mailto:{address}")
            changed = True
    for link in doc.xpath("//a[@href]"):
        href = link.get("href").strip()
        if not href.lower().startswith("mailto:"):
            continue
        address = unquote(href[len("mailto:") :].split("?", 1)[0]).strip()
        if address:
            found.append(address)
        if address and address.lower() not in link.text_content().lower():
            link.tail = f" ({address})" + (link.tail or "")
            changed = True

    unique = list(dict.fromkeys(a.lower() for a in found))
    return (lxml.html.tostring(doc, encoding="unicode") if changed else html), unique


def textify(html: str) -> str:
    """Readable text, or an empty string when there is nothing to read."""
    html, addresses = reveal_addresses(html)
    text = _extract(html)
    missing = [a for a in addresses if a not in text.lower()]
    if missing:
        text = f"{text}\n\nE-Mail: {', '.join(missing)}".strip()
    return text


def _extract(html: str) -> str:
    text = trafilatura.extract(html, favor_recall=True) or ""
    if len(text.split()) < _THIN_WORDS:
        # clean=False matters more than it looks. The default strips what
        # trafilatura considers page furniture — including <div id="footer">,
        # which is where an Impressum lives. Cleaning here would re-apply the
        # very judgement that lost the address block, and the fallback would
        # quietly return nothing at all.
        fallback = trafilatura.html2txt(_CODE.sub(" ", html), clean=False) or ""
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
