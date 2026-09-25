"""The parts of an invitation the model never writes: all of it but one
sentence.

The model writes one sentence, "Ich schreibe Ihnen, weil ...", saying why
this company gets the mail. Everything around it is written here, from the
profile's `[sender]` and `[invitation]` and the contact, and put together by
`assemble` in this order, separated by blank lines:

    routing line        "Zuhanden ... – besten Dank fürs Weiterleiten"
    greeting            "Guten Tag Frau Muster"
    opening             who writes, where, and the request
    topic + sentence    "Es geht darum, ... ." and the model's sentence
    link block          what the survey offers, the link, what it carries
    privacy text        where name and address came from, one mail only
    closing             thanks and signature

so that whatever steered the model cannot change who is greeted, where the
link points, or what the reader is told about their data. The model never
sees the link either, and `check_draft` requires the finished mail to equal
what `assemble` builds from the model's sentence, byte for byte. The design
and its reasons are the frame@1 study (`FRAME_VERSION`); German text lives
only in this module, in Swiss spelling.

The link's shape is fixed by the survey (decided 2026-09-24):
`<survey_url>/?c=<UID>&l=<de|en>`. The survey opens its follow-up questions
only for a UID whose check digit is valid, and reserves `SMOKE` for its own
smoke test, so an invalid UID must fail here rather than produce a link that
looks right and records nothing.
"""

import hashlib
import re
from datetime import date
from typing import Literal, NamedTuple

from company_reach.errors import CompanyReachError
from company_reach.models import Contact
from company_reach.profile import Invitation, Sender

FRAME_VERSION = "frame@1"

_DIGITS = re.compile(r"\D")
# The UID register's weights for the first eight digits (mod 11).
_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4)
_LANGS = ("de", "en")


class InvitationError(CompanyReachError):
    """A link or sentence could not be built from what was given."""


# --- the survey link ---------------------------------------------------------


def _compact(uid: str) -> str | None:
    """`CHE-000.000.046`, `CHE000000046`, `CHE 000 000 046` → `CHE000000046`."""
    text = uid.strip().upper()
    if not text.startswith("CHE"):
        return None
    digits = _DIGITS.sub("", text[3:])
    return f"CHE{digits}" if len(digits) == 9 else None


def uid_is_valid(uid: str) -> bool:
    compact = _compact(uid)
    if compact is None:
        return False
    digits = [int(c) for c in compact[3:]]
    check = 11 - sum(w * d for w, d in zip(_WEIGHTS, digits[:8], strict=True)) % 11
    check = 0 if check == 11 else check
    return check != 10 and check == digits[8]


def survey_link(survey_url: str, uid: str, *, lang: str = "de") -> str:
    if not survey_url:
        raise InvitationError(
            "no survey_url in the profile; every invitation links to it"
        )
    if lang not in _LANGS:
        raise InvitationError(f"lang must be one of {_LANGS}, not {lang!r}")
    if not uid_is_valid(uid):
        raise InvitationError(f"{uid!r} is not a UID with a valid check digit")
    return f"{survey_url.rstrip('/')}/?c={_compact(uid)}&l={lang}"


def arm_for(uid: str, *, experiment: bool) -> Literal["voll", "kurz"]:
    """The length A/B (`[invitation] experiment`): "kurz" leaves out the
    results line and the UID line, which the survey's first page carries
    anyway. The arm is the parity of sha256(UID), so it needs no stored
    state, and a redraft of the same company is always the same arm. With
    the experiment off, every mail is "voll"."""
    if not experiment:
        return "voll"
    key = _compact(uid) or uid
    return "kurz" if int(hashlib.sha256(key.encode()).hexdigest(), 16) % 2 else "voll"


# --- names -------------------------------------------------------------------

_PARTICLES = {
    "von", "van", "de", "di", "da", "del", "della", "le", "la", "zu", "vom",
    "zur", "du",
}  # fmt: skip
# Degrees written without a full stop; every token that ends in one
# ("dipl.", "Ing.", "lic.") is dropped as well.
_DEGREES = {"mba", "msc", "bsc", "ba", "ma", "phd", "fh", "hf", "eth"}
_SALUTATION_WORDS = {"frau", "herr", "herrn"}

# A role noun in its feminine form: the page or the register chose it.
_FEMININE = re.compile(
    r"(inhaber|geschäftsführer|gründer|leiter|präsident|direktor|teilhaber"
    r"|gesellschafter|verleger|unternehmer)in(nen)?\b",
    re.IGNORECASE,
)
# The masculine form counts only from SHAB, which genders its entries. A
# website writes "Inhaber" or "Gründer" for a woman too.
_MASCULINE = re.compile(
    r"\b(inhaber|geschäftsführer|gründer|leiter|präsident|direktor|teilhaber"
    r"|gesellschafter|verleger|unternehmer|vorsitzender)\b",
    re.IGNORECASE,
)


class Name(NamedTuple):
    title: str  # "Prof." or "Dr." or ""
    given: tuple[str, ...]
    surname: str | None  # None when it cannot be told apart


def split_name(full: str) -> Name:
    """Title, given names and surname: `Prof. Dr. Urs Probe` gives
    ("Prof.", ("Urs",), "Probe").

    Only the highest title is kept; degrees are dropped, and so is anything
    after a comma ("Reto Muster, dipl. Ing. FH"). The surname is the second
    of two words, everything from a particle on ("von Arx"), or a
    hyphenated last word after two given names. Any other name of three
    words or more is ambiguous — "Hans Peter Muster" — and has no surname,
    so the mail falls back to the full name rather than guess.
    """
    title = ""
    rest: list[str] = []
    for token in full.split(",")[0].split():
        low = token.lower()
        if low == "prof.":
            title = "Prof."
        elif low.startswith("dr.") and title != "Prof.":
            title = "Dr."
        elif token.endswith(".") or low in _DEGREES or low in _SALUTATION_WORDS:
            continue
        else:
            rest.append(token)
    if len(rest) < 2:
        return Name(title, tuple(rest), None)
    for i, token in enumerate(rest[1:], start=1):
        if token.lower() in _PARTICLES:
            return Name(title, tuple(rest[:i]), " ".join(rest[i:]))
    if len(rest) == 2:
        return Name(title, (rest[0],), rest[1])
    if len(rest) == 3 and "-" in rest[2]:
        return Name(title, tuple(rest[:2]), rest[2])
    return Name(title, tuple(rest), None)


def named(contact: Contact) -> bool:
    """A person can be addressed. A lone first name ("Reto") cannot: it is
    treated as nobody named everywhere in the mail."""
    if not contact.name:
        return False
    name = split_name(contact.name)
    return len(name.given) + (1 if name.surname else 0) >= 2


def salutation(contact: Contact) -> tuple[str | None, str | None]:
    """("Frau" | "Herr" | None, where it came from: "set" | "role" | None).

    In this order: what the reviewer chose or the page wrote before the
    surname (`Contact.salutation`); a feminine role noun; a masculine role
    noun from SHAB. Never a guess from a first name, and never a masculine
    role from a website."""
    if contact.salutation == "ohne":
        return None, "set"
    if contact.salutation:
        return contact.salutation, "set"
    role = contact.role or ""
    if _FEMININE.search(role):
        return "Frau", "role"
    if contact.source == "shab" and _MASCULINE.search(role):
        return "Herr", "role"
    return None, None


def address(contact: Contact, case: Literal["nom", "acc"] = "nom") -> str | None:
    """ "Frau Dr. Beispiel-Keller", "Herrn Probe" (accusative), or the full
    name without a title when there is no salutation or no surname. None
    when nobody can be addressed."""
    if not named(contact):
        return None
    name = split_name(contact.name or "")
    word, _ = salutation(contact)
    if word and name.surname:
        if word == "Herr" and case == "acc":
            word = "Herrn"
        return " ".join(part for part in (word, name.title, name.surname) if part)
    return " ".join([*name.given, *([name.surname] if name.surname else [])])


def falls_back(contact: Contact) -> bool:
    """The greeting uses the full name because no salutation is known: the
    card asks the reviewer to check it ("Anrede prüfen"). A reviewer who
    chose "ohne" has checked it."""
    if not named(contact) or contact.salutation == "ohne":
        return False
    word, _ = salutation(contact)
    return not (word and split_name(contact.name or "").surname)


# --- the parts of the mail ---------------------------------------------------

_WEITERLEITEN = " – besten Dank fürs Weiterleiten"


def greeting(contact: Contact) -> str:
    """No comma after it: the next line starts with a capital. Never "Sehr
    geehrte Damen und Herren", the greeting that gets forwarded unread."""
    who = address(contact)
    return f"Guten Tag {who}" if who else "Guten Tag"


def routing_line(contact: Contact) -> str | None:
    """For whoever opens a shared inbox. None for the person's own address."""
    if contact.email_kind == "seen":
        return None
    if not named(contact):
        return f"Zuhanden der Geschäftsleitung{_WEITERLEITEN}"
    # SHAB describes a past state: the named person may have left
    tail = " oder der Geschäftsleitung" if contact.source == "shab" else ""
    return f"Zuhanden {address(contact, 'acc')}{tail}{_WEITERLEITEN}"


def subject(contact: Contact, sender: Sender, inv: Invitation) -> str:
    """Never "Umfrage", a question or a plea. A named person at a shared
    inbox is named, unless that makes the subject longer than 60."""
    plain = f"Masterarbeit an der {sender.school_short}: Bitte um {inv.minutes} Minuten"
    if contact.email_kind == "seen":
        return plain
    if not named(contact):
        return f"Für die Geschäftsleitung: Masterarbeit an der {sender.school_short}"
    named_subject = (
        f"Für {address(contact, 'acc')}: Masterarbeit an der {sender.school_short}"
    )
    return named_subject if len(named_subject) <= 60 else plain


def opening(sender: Sender, inv: Invitation) -> str:
    where = f"an der {sender.school_short}"
    if sender.place:
        where += f" in {sender.place}"
    return (
        f"Ich heisse {sender.name} und studiere {where}. Für meine Masterarbeit "
        f"wäre ich froh um Ihre Hilfe: Hätten Sie {inv.minutes} Minuten für "
        "einen Fragebogen?"
    )


def topic_line(inv: Invitation) -> str:
    return f"Es geht darum, {inv.topic.rstrip('. ')}."


_WEEKDAYS = (
    "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag",
    "Sonntag",
)  # fmt: skip
_MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
    "September", "Oktober", "November", "Dezember",
)  # fmt: skip


def german_date(day: date) -> str:
    """ "Freitag, 30. Oktober". From tuples, not the locale: a machine
    without a German locale must write the same mail."""
    return f"{_WEEKDAYS[day.weekday()]}, {day.day}. {_MONTHS[day.month - 1]}"


def link_block(link: str, inv: Invitation, *, short: bool = False) -> str:
    """One paragraph, single line breaks. Each part appears only when its
    fact is set in the profile. `short` (the "kurz" arm) leaves out the
    results line and the UID line; the survey's first page carries both."""
    lines = []
    if inv.offer_results and not short:
        lines.append("Als Dank können Sie am Schluss die Ergebnisse anfordern.")
    notes = []
    if inv.no_login:
        notes.append("ohne Anmeldung")
    if inv.closes:
        notes.append(f"offen bis {german_date(inv.closes)}")
    lines.append("Zum Fragebogen" + (f" ({', '.join(notes)})" if notes else "") + ":")
    lines.append(link)
    if not short:
        lines.append(
            "Der Link enthält die UID Ihrer Firma; veröffentlicht werden nur "
            "zusammengefasste Ergebnisse."
        )
    return "\n".join(lines)


# Where the name and the address came from, per (source, address kind). The
# wording is the study's. "lösche ich Ihren Namen", never "beides": `forget`
# deletes the name and keeps the address on the suppression list for good.
# SHAB is never given as the source of an address, and a constructed address
# is described as nothing — it is company data, and read only if it exists.
_NAMED_SOURCES = {
    ("site", "seen"): "Ihren Namen und Ihre Adresse habe ich von Ihrer Website "
    "und nutze beides nur für diese Anfrage.",
    ("site", "generic"): "Ihren Namen und diese Adresse habe ich von Ihrer "
    "Website und nutze beides nur für diese Anfrage.",
    ("site", "constructed"): "Ihren Namen habe ich von Ihrer Website und nutze "
    "ihn nur für diese Anfrage.",
    ("shab", "seen"): "Ihren Namen habe ich aus dem Handelsamtsblatt (SHAB), "
    "Ihre Adresse von Ihrer Website; ich nutze beides nur für diese Anfrage.",
    ("shab", "generic"): "Ihren Namen habe ich aus dem Handelsamtsblatt (SHAB), "
    "diese Adresse von Ihrer Website; ich nutze beides nur für diese Anfrage.",
    ("shab", "constructed"): "Ihren Namen habe ich aus dem Handelsamtsblatt "
    "(SHAB) und nutze ihn nur für diese Anfrage.",
}
_UNNAMED_SITE = (
    "Diese Adresse habe ich von Ihrer Website und nutze sie nur für diese Anfrage."
)
# A lone first name at a guessed info@: nobody is named, and the address was
# never on the site, so the text claims nothing about where it came from.
_UNNAMED_CONSTRUCTED = "Diese Adresse nutze ich nur für diese Anfrage."
_NEIN = "Ein kurzes «Nein» genügt, dann lösche ich Ihren Namen."


def privacy(contact: Contact, inv: Invitation) -> str:
    once = (
        "Ich erinnere Sie höchstens einmal daran."
        if inv.reminder
        else "Ich schreibe Ihnen nur dieses eine Mal."
    )
    kind = contact.email_kind
    if kind not in ("seen", "generic", "constructed"):
        raise InvitationError(f"no invitation is written to a {kind} address")
    if not named(contact):
        first = _UNNAMED_CONSTRUCTED if kind == "constructed" else _UNNAMED_SITE
        return f"{first} {once}"
    return f"{_NAMED_SOURCES[(contact.source, kind)]} {once} {_NEIN}"


def closing(sender: Sender, inv: Invitation) -> str:
    """With `sign_in_body` false only the first line is written, for a mail
    client that adds the same signature itself."""
    lines = ["Vielen Dank und freundliche Grüsse"]
    if inv.sign_in_body:
        lines += [sender.name, sender.affiliation]
        if sender.supervisor:
            lines.append(f"Betreut von {sender.supervisor}")
    return "\n".join(lines)


def assemble(
    contact: Contact,
    sentence: str,
    *,
    link: str,
    sender: Sender,
    inv: Invitation,
    short: bool = False,
) -> str:
    """The whole body around the model's sentence, in frame order. The
    sentence is the only part the model wrote; its ß becomes ss here."""
    parts = [
        routing_line(contact),
        greeting(contact),
        opening(sender, inv),
        f"{topic_line(inv)} {sentence.strip().replace('ß', 'ss')}",
        link_block(link, inv, short=short),
        privacy(contact, inv),
        closing(sender, inv),
    ]
    return "\n\n".join(part for part in parts if part)
