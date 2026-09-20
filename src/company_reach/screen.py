"""Cheap, deterministic exclusion of companies that are certainly not
operating businesses. No network, no model. Measured rules from LEARNINGS §2:
the purpose text excludes, it does not classify — so this stays conservative."""

import re

from company_reach.models import CompanyRecord

_TAIL = re.compile(r"\s*(Die Gesellschaft kann|Sie kann)\b.*", re.S)
_PROPERTY = re.compile(r"Liegenschaft|Immobilie|Grundstück|Grundeigentum", re.I)
# Concrete signals of an operating business. Dienstleistung/Entwicklung are
# deliberately absent: property firms use both (LEARNINGS §2).
_OPERATING = re.compile(
    r"Herstellung|Produktion|Fabrikation|Fertigung|Montage|Reparatur|Ingenieur"
    r"|Maschinen|Werkstatt|Labor|Import|Export|Betrieb eine[sr]|Betrieb von"
    r"|Handel mit (?!Immobilien|Liegenschaft)",
    re.I,
)


def head_clause(purpose: str) -> str:
    """The purpose text without the boilerplate tail (96.4% carry one)."""
    return _TAIL.sub("", purpose).strip()


def screen_reason(record: CompanyRecord) -> str | None:
    """None to keep the company; otherwise the reason it is dropped."""
    if "in liquidation" in record.name.lower():
        return "in liquidation"
    head = record.purpose_head
    if _PROPERTY.search(head) and not _OPERATING.search(head):
        return "property only"
    return None
