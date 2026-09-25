"""The parts of an invitation the model never writes: the frame around its
one sentence — routing line, greeting, opening, topic, link block, privacy
text and closing — and the survey link. All are written by code, so page
text cannot steer any of them."""

import json
import re
from datetime import date
from pathlib import Path

import pytest

from company_reach.models import Contact
from company_reach.profile import Invitation, Sender
from company_reach.tools.invitation import (
    InvitationError,
    arm_for,
    assemble,
    closing,
    german_date,
    greeting,
    link_block,
    needs_check,
    opening,
    privacy,
    routing_line,
    salutation,
    split_name,
    subject,
    survey_link,
    uid_is_valid,
)

URL = "https://survey.example/form"
# CHE-000.000.046: the check digit of 00000004 under the UID weights is 6
UID = "CHE000000046"


# --- the UID -----------------------------------------------------------------


def test_a_register_uid_passes_its_check_digit():
    assert uid_is_valid("CHE-000.000.046")
    assert uid_is_valid("CHE-123.456.788")


def test_a_wrong_check_digit_fails():
    assert not uid_is_valid("CHE-000.000.047")


def test_the_check_digit_ten_never_exists():
    # 11 - (sum mod 11) = 10 has no digit, so such a number is never issued:
    # 00000003 weighs 3 x 4 = 12, and 11 - (12 mod 11) = 10
    assert not any(uid_is_valid(f"CHE00000003{d}") for d in "0123456789")


def test_anything_but_che_and_nine_digits_fails():
    assert not uid_is_valid("SMOKE")
    assert not uid_is_valid("CHE-000.000.04")
    assert not uid_is_valid("DEU000000046")


# --- the link ----------------------------------------------------------------


@pytest.mark.parametrize(
    "written", ["CHE-000.000.046", "CHE000000046", "CHE 000 000 046"]
)
def test_every_way_of_writing_the_uid_gives_the_same_link(written: str):
    assert survey_link(URL, written) == f"{URL}/?c={UID}&l=de"


def test_a_trailing_slash_is_not_doubled():
    assert survey_link(f"{URL}/", UID) == f"{URL}/?c={UID}&l=de"


def test_english_is_the_other_language():
    assert survey_link(URL, UID, lang="en").endswith("&l=en")


def test_no_third_language():
    with pytest.raises(InvitationError, match="lang"):
        survey_link(URL, UID, lang="fr")


def test_an_invalid_uid_never_reaches_a_link():
    # the survey opens its follow-up questions only for a valid UID; an
    # invitation carrying a broken one would look fine and record nothing
    with pytest.raises(InvitationError, match="check digit"):
        survey_link(URL, "CHE-000.000.047")


def test_the_smoke_tag_is_never_an_invitation():
    # reserved for the survey's own smoke test
    with pytest.raises(InvitationError):
        survey_link(URL, "SMOKE")


def test_no_survey_url_is_an_error():
    with pytest.raises(InvitationError, match="survey_url"):
        survey_link("", UID)


# --- the frame (frame@1) -----------------------------------------------------
#
# Every name below is fictional. The five complete mails are the study's
# examples, kept in tests/fixtures/invitation/examples.json.

SENDER = Sender(
    name="Lena Brunner",
    affiliation="Masterstudentin, OST Ostschweizer Fachhochschule",
    school_short="OST",
    place="St. Gallen",
    supervisor="Prof. Dr. Hans Vorbild",
)
INV = Invitation(
    topic="wie KMU zu Kunden und Lieferanten kommen",
    closes=date(2026, 10, 30),
    offer_results=True,
    no_login=True,
)
EXAMPLES = json.loads(
    (Path(__file__).parent / "fixtures/invitation/examples.json").read_text()
)
LINK = "https://survey.example/form/?c=CHE000000046&l=de"


def person(
    name: str | None,
    role: str | None = None,
    *,
    kind: str = "generic",
    source: str = "site",
    salutation: str | None = None,
    origin: str | None = None,
) -> Contact:
    return Contact(
        name=name,
        role=role,
        email="info@muster.example",
        email_kind=kind,
        source=source,
        salutation=salutation,
        salutation_origin=origin,
    )


@pytest.mark.parametrize(
    ("written", "title", "given", "surname"),
    [
        ("Reto Muster", "", ("Reto",), "Muster"),
        ("Sandra Beispiel-Keller", "", ("Sandra",), "Beispiel-Keller"),
        ("Hans von Arx", "", ("Hans",), "von Arx"),
        ("Maria van der Berg", "", ("Maria",), "van der Berg"),
        ("Anna Maria Beispiel-Keller", "", ("Anna", "Maria"), "Beispiel-Keller"),
        # three plain tokens: which two are given names is not knowable
        ("Hans Peter Muster", "", ("Hans", "Peter", "Muster"), None),
        ("Prof. Dr. Urs Probe", "Prof.", ("Urs",), "Probe"),
        ("Dr. Sandra Beispiel-Keller", "Dr.", ("Sandra",), "Beispiel-Keller"),
        ("Reto Muster, dipl. Ing. FH", "", ("Reto",), "Muster"),
        ("dipl. Ing. Reto Muster MBA", "", ("Reto",), "Muster"),
        ("Frau Anna Muster", "", ("Anna",), "Muster"),
        ("Reto", "", ("Reto",), None),
    ],
)
def test_split_name(written, title, given, surname):
    assert split_name(written) == (title, given, surname)


def test_a_masculine_site_role_never_sets_herr():
    # sites use the masculine role generically; "Gründer" may be a woman
    c = person("Marco Vorlage", "Gründer")
    assert salutation(c) == (None, None)
    assert greeting(c) == "Guten Tag Marco Vorlage"
    assert needs_check(c)


def test_a_feminine_role_sets_frau():
    c = person("Dr. Sandra Beispiel-Keller", "Geschäftsführerin")
    assert salutation(c) == ("Frau", "role")
    assert greeting(c) == "Guten Tag Frau Dr. Beispiel-Keller"
    assert needs_check(c)  # a role only proposes


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("Präsident des Verwaltungsrates", "Herr"),
        ("Präsidentin des Verwaltungsrates", "Frau"),
        ("Geschäftsführer", "Herr"),
        ("Mitglied des Verwaltungsrates", None),
    ],
)
def test_a_gendered_shab_role_proposes_a_salutation(role, expected):
    # SHAB genders its entries, so its masculine form is evidence
    c = person("Urs Probe", role, source="shab")
    assert salutation(c) == (expected, "shab" if expected else None)
    assert needs_check(c)


def test_the_reviewers_choice_wins_over_the_role():
    chosen = person("Reto Muster", "Inhaberin", salutation="Herr", origin="reviewer")
    assert salutation(chosen) == ("Herr", "reviewer")
    none = person("Anna Muster", "Inhaberin", salutation="ohne", origin="reviewer")
    assert salutation(none) == (None, "reviewer")
    assert greeting(none) == "Guten Tag Anna Muster"


@pytest.mark.parametrize(
    ("value", "origin", "checked"),
    [
        ("Frau", "reviewer", True),
        ("ohne", "reviewer", True),
        ("Frau", "page", True),  # "Frau Anna Muster" written out on the page
        ("Frau", "page-surname", False),  # "Frau Muster": which Muster?
        ("Frau", None, False),  # stored before origins were: not known
    ],
)
def test_only_the_reviewer_or_a_full_page_match_is_certain(value, origin, checked):
    c = person("Anna Muster", None, salutation=value, origin=origin)
    assert needs_check(c) is not checked


def test_a_reviewers_ohne_is_checked():
    # the full name stays, but someone looked: the card stops asking
    assert needs_check(person("Marco Vorlage", "Gründer"))
    assert not needs_check(
        person("Marco Vorlage", "Gründer", salutation="ohne", origin="reviewer")
    )


def test_herr_becomes_herrn_in_subject_and_routing():
    c = person("Urs Probe", "Präsident des Verwaltungsrates", source="shab")
    assert greeting(c) == "Guten Tag Herr Probe"
    assert subject(c, SENDER, INV) == "Für Herrn Probe: Masterarbeit an der OST"
    assert routing_line(c) == (
        "Zuhanden Herrn Probe oder der Geschäftsleitung – besten Dank fürs Weiterleiten"
    )


def test_a_lone_first_name_is_treated_as_nobody_named():
    c = person("Reto", "CEO")
    assert greeting(c) == "Guten Tag"
    assert routing_line(c) == (
        "Zuhanden der Geschäftsleitung – besten Dank fürs Weiterleiten"
    )
    assert subject(c, SENDER, INV) == (
        "Für die Geschäftsleitung: Masterarbeit an der OST"
    )
    assert "Ihren Namen" not in privacy(c, INV)
    assert not needs_check(c)


def test_no_routing_line_for_a_seen_address():
    c = person("Reto Muster", "Inhaber", kind="seen", salutation="Herr")
    assert routing_line(c) is None
    assert subject(c, SENDER, INV) == "Masterarbeit an der OST: Bitte um 15 Minuten"


def test_shab_routing_adds_geschaeftsleitung():
    site = person("Anna Muster", "Inhaberin")
    shab = person("Anna Muster", "Inhaberin", source="shab")
    assert "oder der Geschäftsleitung" not in routing_line(site)
    assert routing_line(shab) == (
        "Zuhanden Frau Muster oder der Geschäftsleitung – besten Dank fürs Weiterleiten"
    )


def test_a_name_without_a_salutation_is_written_out_in_full():
    c = person("Hans Peter Muster", "Inhaber")
    assert routing_line(c).startswith("Zuhanden Hans Peter Muster – ")
    assert subject(c, SENDER, INV) == ("Für Hans Peter Muster: Masterarbeit an der OST")


def test_subject_over_60_chars_falls_back():
    c = person("Dr. Katharina Beispiel-Hinterberger-Muster", "Inhaberin")
    long = "Für Frau Dr. Beispiel-Hinterberger-Muster: Masterarbeit an der OST"
    assert len(long) > 60
    assert subject(c, SENDER, INV) == "Masterarbeit an der OST: Bitte um 15 Minuten"


_EINMAL = "Ich schreibe Ihnen nur dieses eine Mal."
_NEIN = "Ein kurzes «Nein» genügt, dann lösche ich Ihren Namen."


@pytest.mark.parametrize(
    ("contact", "first"),
    [
        (
            person("Reto Muster", "Inhaber", kind="seen"),
            "Ihren Namen und Ihre Adresse habe ich von Ihrer Website und nutze "
            "beides nur für diese Anfrage.",
        ),
        (
            person("Anna Muster", "Inhaberin", kind="generic"),
            "Ihren Namen und diese Adresse habe ich von Ihrer Website und nutze "
            "beides nur für diese Anfrage.",
        ),
        (
            person("Anna Muster", "Inhaberin", kind="constructed"),
            "Ihren Namen habe ich von Ihrer Website und nutze ihn nur für diese "
            "Anfrage.",
        ),
        (
            person("Urs Probe", "Präsident", kind="generic", source="shab"),
            "Ihren Namen habe ich aus dem Handelsamtsblatt (SHAB), diese Adresse "
            "von Ihrer Website; ich nutze beides nur für diese Anfrage.",
        ),
        (
            person("Urs Probe", "Präsident", kind="constructed", source="shab"),
            "Ihren Namen habe ich aus dem Handelsamtsblatt (SHAB) und nutze ihn "
            "nur für diese Anfrage.",
        ),
        (
            person("Urs Probe", "Präsident", kind="seen", source="shab"),
            "Ihren Namen habe ich aus dem Handelsamtsblatt (SHAB), Ihre Adresse "
            "von Ihrer Website; ich nutze beides nur für diese Anfrage.",
        ),
    ],
)
def test_privacy_text_per_kind(contact, first):
    text = privacy(contact, INV)
    assert text == f"{first} {_EINMAL} {_NEIN}"
    # forget deletes the name and keeps the address suppressed for good, so
    # the promise is about the name, never "beides"
    assert "lösche ich Ihren Namen" in text
    assert "lösche ich beides" not in text
    # SHAB names people; it is never where an address came from
    for clause in re.split(r"[,;.]", text):
        if "Adresse" in clause:
            assert "Website" in clause
            assert "SHAB" not in clause


def test_privacy_text_when_nobody_is_named():
    text = privacy(person(None), INV)
    assert text == (
        "Diese Adresse habe ich von Ihrer Website und nutze sie nur für diese "
        f"Anfrage. {_EINMAL}"
    )
    assert "Ihren Namen" not in text


def test_a_guessed_address_is_never_said_to_come_from_the_site():
    # a lone first name at a constructed info@: nobody named, and the
    # address was never on the site
    text = privacy(person("Reto", "Inhaber", kind="constructed"), INV)
    assert "Website" not in text
    assert "Ihren Namen" not in text


def test_the_reminder_wording_replaces_the_one_mail_promise():
    reminded = INV.model_copy(update={"reminder": True})
    text = privacy(person("Anna Muster", "Inhaberin"), reminded)
    assert "Ich erinnere Sie höchstens einmal daran." in text
    assert "nur dieses eine Mal" not in text


def test_german_date_is_friday_30_oktober_for_2026_10_30():
    assert german_date(date(2026, 10, 30)) == "Freitag, 30. Oktober"
    assert german_date(date(2027, 3, 1)) == "Montag, 1. März"


def test_link_block_omits_what_is_not_true():
    bare = Invitation(topic="t")
    assert link_block(LINK, bare) == (
        f"Zum Fragebogen:\n{LINK}\nDer Link enthält die UID Ihrer Firma; "
        "veröffentlicht werden nur zusammengefasste Ergebnisse."
    )
    assert link_block(LINK, INV).startswith(
        "Als Dank können Sie am Schluss die Ergebnisse anfordern.\n"
        "Zum Fragebogen (ohne Anmeldung, offen bis Freitag, 30. Oktober):\n"
    )


def test_the_short_link_block_leaves_out_results_and_uid_lines():
    assert link_block(LINK, INV, short=True) == (
        f"Zum Fragebogen (ohne Anmeldung, offen bis Freitag, 30. Oktober):\n{LINK}"
    )


def test_the_opening_names_sender_school_and_request():
    assert opening(SENDER, INV) == (
        "Ich heisse Lena Brunner und studiere an der OST in St. Gallen. Für "
        "meine Masterarbeit wäre ich froh um Ihre Hilfe: Hätten Sie 15 Minuten "
        "für einen Fragebogen?"
    )
    nowhere = SENDER.model_copy(update={"place": ""})
    assert "an der OST. Für" in opening(nowhere, INV)


def test_the_closing_signs_only_what_is_set():
    assert closing(SENDER, INV) == (
        "Vielen Dank und freundliche Grüsse\nLena Brunner\n"
        "Masterstudentin, OST Ostschweizer Fachhochschule\n"
        "Betreut von Prof. Dr. Hans Vorbild"
    )
    alone = SENDER.model_copy(update={"supervisor": ""})
    assert "Betreut" not in closing(alone, INV)
    client_signs = INV.model_copy(update={"sign_in_body": False})
    assert closing(SENDER, client_signs) == "Vielen Dank und freundliche Grüsse"


def test_the_sentence_loses_its_eszett():
    body = assemble(
        person(None),
        "Ich schreibe Ihnen, weil Ihre Firma Strassen und Grossküchen baut.".replace(
            "ss", "ß"
        ),
        link=LINK,
        sender=SENDER,
        inv=INV,
    )
    assert "ß" not in body
    assert "Grossküchen" in body


def test_the_arm_is_stable_for_a_uid():
    # sha256(uid) parity: the same company always gets the same arm, so a
    # redraft keeps it, whatever the spelling of the UID
    assert arm_for("CHE000000046", experiment=True) == "voll"
    assert arm_for("CHE900000016", experiment=True) == "kurz"
    assert arm_for("CHE-900.000.016", experiment=True) == "kurz"
    # with the experiment off, every mail is the full one
    assert arm_for("CHE900000016", experiment=False) == "voll"


def test_the_kurz_arm_is_the_voll_mail_without_two_lines():
    c = person("Anna Muster", "Inhaberin")
    voll = assemble(c, "Ich schreibe Ihnen, weil x.", link=LINK, sender=SENDER, inv=INV)
    kurz = assemble(
        c, "Ich schreibe Ihnen, weil x.", link=LINK, sender=SENDER, inv=INV, short=True
    )
    gone = {
        "Als Dank können Sie am Schluss die Ergebnisse anfordern.",
        "Der Link enthält die UID Ihrer Firma; veröffentlicht werden nur "
        "zusammengefasste Ergebnisse.",
    }
    assert set(voll.split("\n")) - set(kurz.split("\n")) == gone


@pytest.mark.parametrize("mail", EXAMPLES["mails"], ids=lambda m: m["kind"])
def test_the_five_example_mails_are_assembled_byte_for_byte(mail):
    c = Contact(**mail["contact"])
    link = survey_link(EXAMPLES["survey_url"], EXAMPLES["uid"])
    assert subject(c, SENDER, INV) == mail["subject"]
    body = assemble(c, mail["sentence"], link=link, sender=SENDER, inv=INV)
    assert body == mail["body"]
    assert "ß" not in body
