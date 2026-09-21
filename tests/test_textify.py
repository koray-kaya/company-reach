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
