"""HTML to text, and the normalisation every quote check runs through.

trafilatura is built for articles: it finds the main body and throws the
furniture away. An Impressum *is* furniture — a footer block with the
address and the UID — so on exactly the pages this project cares about it can
return two dozen words and drop the lines tier 1 and tier 2 need. Measured in
the probe: one Impressum came back as 29 words while a plain conversion kept
the address block (`research/website-reading.md:53`).

Hence the rule: extract with recall favoured, and if that is thin, fall back.
And whatever the extractor kept, the page's footer is read on its own: a
one-page site keeps its Impressum there, and once the main text is long
enough the fallback never runs (audit). A footer the text lacks is put in
front of it, so that `read_pages`, cutting a long page to length, takes the
main text's tail and never the Impressum.

E-mail addresses get lost three ways, all measured in the M6 live run. Both
extractors keep what a link *says* and drop where it points, and a site
writes "E-Mail" or a person's name and puts the address in `mailto:`.
Cloudflare encodes addresses so the page itself says "[email protected]".
And a contact block is furniture too — an `<address>` in the header, a
"quick contact" bar — so a visible address goes with it. So the addresses
are collected from the HTML with lxml (the parser trafilatura runs on),
written in beside their link text, and any the extracted text still lacks
are added in front of it, for the same reason. Every one of them is a string
the page carries.
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
# Where a site keeps what it repeats on every page: the <footer>, an
# <address>, and the blocks themes name "footer" or "impressum" — as a whole
# word of an id or class ("site-footer", not "prefooter").
_FOOTER_WORDS = {"footer", "impressum"}
_WORD_SPLIT = re.compile(r"[\s_-]+")
# A block longer than this is a wrapper around the page, not its footer.
_MAX_FOOTER_CHARS = 3000


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
    main = _extract(html)
    # what the extractor dropped goes in front, where a cut for length
    # cannot reach it
    lacking = [b for b in _footer_blocks(html) if normalise(b) not in normalise(main)]
    seen = "\n".join([*lacking, main]).lower()
    missing = [a for a in addresses if a not in seen]
    if missing:
        lacking.append(f"E-Mail: {', '.join(missing)}")
    return "\n\n".join([*lacking, main]).strip()


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


def _is_footer(element) -> bool:
    if element.tag in ("footer", "address"):
        return True
    if element.tag in ("html", "body"):  # `<body class="has-sticky-footer">`
        return False
    named = f"{element.get('id', '')} {element.get('class', '')}".lower()
    return bool(_FOOTER_WORDS.intersection(_WORD_SPLIT.split(named)))


def _footer_blocks(html: str) -> list[str]:
    """The text of each footer-like block, the outermost one only when they
    nest, in page order. Kept as furniture, not cleaned: the cleaning is
    what drops a footer in the first place.

    A block is not a footer when it holds the page's `<main>` or `<article>`,
    or runs past `_MAX_FOOTER_CHARS`: a theme wrapping the whole page in a
    div it calls "impressum" would otherwise have the page added to itself.
    A footer inside such a wrapper is still found."""
    try:
        doc = lxml.html.fromstring(_CODE.sub(" ", html))
    except (ParserError, ValueError):
        return []
    kept: set = set()
    blocks: list[str] = []
    for element in doc.iter():  # document order: a parent before its children
        if not isinstance(element.tag, str) or not _is_footer(element):
            continue
        if any(parent in kept for parent in element.iterancestors()):
            continue
        if element.xpath(".//main | .//article"):
            continue
        text = trafilatura.html2txt(element, clean=False)
        if text and len(text) <= _MAX_FOOTER_CHARS:
            kept.add(element)
            blocks.append(text)
    return blocks


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
