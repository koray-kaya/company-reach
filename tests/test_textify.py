"""HTML to text, and the normalisation the verbatim-quote check depends on."""

from company_reach.tools.textify import normalise, textify

ARTICLE = (
    "<html><body><article><h1>Muster Metallbau</h1>"
    + "<p>"
    + "Wir fertigen Metallteile fuer den Maschinenbau. " * 20
    + "</p>"
    + "</article></body></html>"
)

IMPRESSUM = (
    "<html><body><div id='footer'><h2>Impressum</h2>"
    "<p>Muster Metallbau AG<br>Beispielstrasse 1<br>8000 Musterstadt<br>"
    "CHE-000.000.046 MWST</p></div></body></html>"
)


def test_a_normal_page_gives_its_text():
    text = textify(ARTICLE)
    assert "Metallteile" in text
    assert "<p>" not in text


def test_a_thin_page_falls_back_and_keeps_the_address():
    """trafilatura optimises for articles. On an Impressum it can return a
    couple of dozen words and drop the address block — which is exactly the
    block tier 2 needs. Under ~80 words we fall back to html2txt."""
    text = textify(IMPRESSUM)
    assert "8000 Musterstadt" in text
    assert "CHE-000.000.046" in text


def test_typography_cannot_break_a_quote_check():
    """The model quotes from the text it was given; the code then looks for
    that quote in the page. Both sides go through this, so a non-breaking
    space or a sharp s cannot make a true quote look invented."""
    assert normalise("Beispielstrasse 1") == normalise("Beispielstrasse 1")
    assert normalise("Strasse") == normalise("Straße")
    assert normalise("a  \n b") == normalise("a b")


def test_scripts_and_styles_are_not_text():
    """The fallback keeps page furniture on purpose, for the Impressum's
    sake — but a script is not furniture. A Wix home page came back as its
    own JavaScript, and the model was shown code instead of the company."""
    html = (
        "<html><head><style>.fc-header { width: 186px; }</style></head><body>"
        "<script>var bodyCacheable = true;</script>"
        "<div id='footer'>Muster Metallbau AG, Beispielstrasse 1, 8000 Musterstadt"
        "</div></body></html>"
    )
    text = textify(html)
    assert "Muster Metallbau AG" in text
    assert "bodyCacheable" not in text
    assert "186px" not in text


# --- the footer --------------------------------------------------------------
# A one-page site keeps its Impressum in the footer. Once the main text has
# 80 words the fallback never runs, and the extractor drops the footer as
# furniture: name, address and UID were gone (audit).

FOOTER = (
    "<p>Inhaber: Hans Muster</p>"
    "<p>Muster Metallbau AG, Beispielstrasse 1, 8000 Musterstadt</p>"
    "<p>UID: CHE-000.000.046</p>"
)


def long_page(footer: str) -> str:
    main = "<p>" + "Wir fertigen Metallteile fuer den Maschinenbau. " * 20 + "</p>"
    body = f"<main><h1>Muster Metallbau</h1>{main}</main>{footer}"
    return f"<html><body>{body}</body></html>"


def test_the_footer_survives_a_long_main_text():
    text = textify(long_page(f"<footer>{FOOTER}</footer>"))
    assert "Metallteile" in text
    for line in (
        "Hans Muster",
        "Beispielstrasse 1, 8000 Musterstadt",
        "CHE-000.000.046",
    ):
        assert line in text


def test_a_footer_div_survives_with_its_visible_address():
    footer = f"<div id='footer'>{FOOTER}<p>info@muster-metallbau.ch</p></div>"
    text = textify(long_page(footer))
    assert "Hans Muster" in text
    assert "info@muster-metallbau.ch" in text


def test_a_footer_the_text_already_has_is_not_written_twice():
    text = textify(IMPRESSUM)
    assert text.count("8000 Musterstadt") == 1


MAIN_SENTENCE = "Wir fertigen Metallteile fuer den Maschinenbau."


def test_a_body_named_after_its_footer_is_not_a_footer():
    """Review of E4: `<body class="has-sticky-footer">` matched, and the
    whole page was appended to itself."""
    html = long_page(f"<footer>{FOOTER}</footer>").replace(
        "<body>", "<body class='has-sticky-footer'>"
    )
    text = textify(html)
    assert text.count(MAIN_SENTENCE) == 20
    assert "CHE-000.000.046" in text


def test_a_wrapper_around_the_main_text_is_not_a_footer():
    # an Impressum page's theme wraps its navigation and the text in one div
    nav = "<nav><a href='/'>Home</a> <a href='/kontakt'>Kontakt</a></nav>"
    html = (
        long_page(f"<footer>{FOOTER}</footer>")
        .replace("<main>", f"<div id='impressum'>{nav}<main>")
        .replace("</main>", "</main></div>")
    )
    text = textify(html)
    assert text.count(MAIN_SENTENCE) == 20
    assert "CHE-000.000.046" in text


def test_a_class_is_matched_on_whole_words():
    # "prefooter" is a banner above the footer, not the footer
    html = long_page(
        "<div class='prefooter'><p>Jetzt Offerte anfragen und profitieren</p></div>"
    )
    assert "Offerte" not in textify(html)


def test_the_footer_comes_before_the_main_text():
    # so that a cut for length takes the main text's tail, never the Impressum
    text = textify(long_page(f"<footer>{FOOTER}</footer>"))
    assert text.index("CHE-000.000.046") < text.index(MAIN_SENTENCE)


# --- addresses the text would otherwise lose ---------------------------------
# Found in the M6 live run: a site whose Impressum carries its address only as
# a mailto: link behind the word "E-Mail", and one whose contact page has it
# only in Cloudflare's encoded form. The text had neither, so a published
# inbox was recorded as a guess.

# Cloudflare XORs every byte with the first one: "5a" is the key, the rest
# is "info@muster-metallbau.ch".
CF_HEX = "5a33343c351a372f292e3f2877373f2e3b3636383b2f743932"


def page(body: str) -> str:
    return f"<html><body><div id='footer'><h2>Impressum</h2>{body}</div></body></html>"


def test_an_address_only_in_a_mailto_link_is_kept():
    html = page(
        "<p>Muster Metallbau AG, Beispielstrasse 1, 8000 Musterstadt</p>"
        "<p><a href='mailto:info@muster-metallbau.ch?subject=Anfrage'>E-Mail</a></p>"
    )
    assert "info@muster-metallbau.ch" in textify(html)


def test_a_name_linked_to_its_address_keeps_both():
    # the way a personal address hides: the visible text is the name
    html = page(
        "<p><a href='mailto:anna.muster@muster-metallbau.ch'>Anna Muster</a></p>"
    )
    text = textify(html)
    assert "Anna Muster" in text
    assert "anna.muster@muster-metallbau.ch" in text


def test_a_visible_address_is_not_written_twice():
    html = page(
        "<p><a href='mailto:info@muster-metallbau.ch'>info@muster-metallbau.ch</a></p>"
    )
    assert textify(html).count("info@muster-metallbau.ch") == 1


def test_an_encoded_cloudflare_address_is_decoded():
    html = page(
        "<p>Kontakt: <a href='/cdn-cgi/l/email-protection' class='__cf_email__' "
        f"data-cfemail='{CF_HEX}'>[email&#160;protected]</a></p>"
    )
    text = textify(html)
    assert "info@muster-metallbau.ch" in text
    assert "protected" not in text


def test_a_cloudflare_link_to_an_encoded_address_is_decoded():
    html = page(
        f"<p><a href='/cdn-cgi/l/email-protection#{CF_HEX}'>Schreiben Sie uns</a></p>"
    )
    assert "info@muster-metallbau.ch" in textify(html)


def test_broken_cloudflare_data_is_left_alone():
    html = page("<p><span data-cfemail='zz'>[email protected]</span> Beispiel</p>")
    assert "Beispiel" in textify(html)


def test_an_address_in_a_block_the_extractor_drops_is_kept():
    # found live: the article is long enough that the fallback never runs,
    # and the contact block sits in <address> inside a header, which the
    # extractor treats as furniture
    html = (
        "<html><body><header><address><a href='mailto:info@muster-metallbau.ch'>"
        "info@muster-metallbau.ch</a></address></header>"
        + ARTICLE.removeprefix("<html><body>")
    )
    text = textify(html)
    assert "Metallteile" in text
    assert "info@muster-metallbau.ch" in text
