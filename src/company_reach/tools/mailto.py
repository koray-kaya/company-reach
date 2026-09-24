"""`mailto:` links, RFC 6068, built with the standard library's `quote`.

Sending stays human: the review page opens the reviewer's own mail client
with this link, and the mail leaves from their account when they press send.
So the link has to carry the draft exactly — umlauts, line breaks and an
ampersand that would otherwise end the body early — and it has to open.
Browsers and mail clients disagree on how long a link may be; 2,000
encoded characters is the length the design settled on as safe for all of
them (`design.md:236`).
"""

from dataclasses import dataclass
from urllib.parse import quote

LIMIT = 2000


@dataclass(frozen=True)
class MailtoLink:
    href: str
    length: int  # of the encoded href, which is what a client has to accept
    fits: bool


def _encode(text: str) -> str:
    # RFC 6068 §5: line breaks are CRLF; safe="" so &, = and ? are encoded
    # and "+" is left meaning a plus, not a space
    return quote(text.replace("\r\n", "\n").replace("\n", "\r\n"), safe="")


def build(to: str, subject: str, body: str) -> MailtoLink:
    href = (
        f"mailto:{quote(to, safe='@')}?subject={_encode(subject)}&body={_encode(body)}"
    )
    return MailtoLink(href=href, length=len(href), fits=len(href) < LIMIT)
