"""`mailto:` links (RFC 6068): the draft has to survive the trip into a mail
client exactly as written, and the link has to be short enough to open."""

from urllib.parse import parse_qs, unquote, urlsplit

from company_reach.tools.mailto import LIMIT, build

TO = "anna.muster@muster-metallbau.ch"
BODY = "Grüezi Frau Muster\n\nFragen & Antworten zur Fertigung.\nFreundliche Grüsse"


def parts(href: str) -> tuple[str, dict[str, list[str]]]:
    split = urlsplit(href)
    return unquote(split.path), parse_qs(split.query)


def test_umlauts_newlines_and_ampersands_round_trip():
    link = build(TO, "Umfrage: Fertigung in der Schweiz", BODY)
    to, query = parts(link.href)
    assert link.href.startswith("mailto:")
    assert to == TO
    assert query["subject"] == ["Umfrage: Fertigung in der Schweiz"]
    # an unencoded & would end the body at "Fragen "
    assert query["body"] == [BODY.replace("\n", "\r\n")]


def test_line_breaks_are_crlf_and_spaces_are_not_plus():
    # RFC 6068 §5: line breaks in a body are %0D%0A; "+" is a literal plus
    href = build(TO, "a b", "one\ntwo").href
    assert "%0D%0A" in href
    assert "+" not in href and "%20" in href


def test_the_length_is_measured_on_the_encoded_link():
    link = build(TO, "Grüsse", "ä" * 100)
    # each ä is two UTF-8 bytes, each byte three characters once encoded
    assert link.length == len(link.href)
    assert link.length > 600


def test_fits_is_false_just_above_the_limit():
    prefix = len(build(TO, "s", "").href)
    at_limit = build(TO, "s", "x" * (LIMIT - 1 - prefix))
    above = build(TO, "s", "x" * (LIMIT - prefix))
    assert (at_limit.length, at_limit.fits) == (LIMIT - 1, True)
    assert (above.length, above.fits) == (LIMIT, False)
