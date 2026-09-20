"""Companies of one municipality from LINDAS (the Swiss federal linked-data
service) with one SPARQL query, paged by ORDER BY ?uid LIMIT/OFFSET.
httpx does the HTTP; the result is the standard SPARQL JSON shape
(head.vars + results.bindings). Attribution to Zefix/LINDAS is required —
see NOTICE."""

import time

import httpx

from company_reach import __version__
from company_reach.models import CompanyRecord
from company_reach.screen import head_clause

GRAPH = "https://lindas.admin.ch/foj/zefix"
_LEGAL_FORMS = ("0106", "0107")  # AG, GmbH
_UA = f"company-reach/{__version__} (+https://github.com/koray-kaya/company-reach)"


class LindasError(RuntimeError):
    pass


def build_query(municipality: str, limit: int, offset: int) -> str:
    forms = ", ".join(
        f"<https://ld.admin.ch/ech/97/legalforms/{f}>" for f in _LEGAL_FORMS
    )
    return f"""
PREFIX schema: <http://schema.org/>
PREFIX admin: <https://schema.ld.admin.ch/>
SELECT ?uid ?name ?lf ?desc ?street ?plz ?city WHERE {{
  GRAPH <{GRAPH}> {{
    ?c a admin:ZefixOrganisation ;
       admin:municipality <https://ld.admin.ch/municipality/{municipality}> ;
       schema:additionalType ?lfIri ;
       schema:legalName ?name ;
       schema:identifier ?id .
    ?id schema:name "CompanyUID" ; schema:value ?uid .
    FILTER(?lfIri IN ({forms}))
    OPTIONAL {{ ?c schema:description ?desc }}
    OPTIONAL {{ ?c schema:address ?a .
      OPTIONAL {{ ?a schema:streetAddress ?street }}
      OPTIONAL {{ ?a schema:postalCode ?plz }}
      OPTIONAL {{ ?a schema:addressLocality ?city }} }}
    BIND(REPLACE(STR(?lfIri), "^.*/", "") AS ?lf)
  }}
}} ORDER BY ?uid LIMIT {limit} OFFSET {offset}
""".strip()


def _post(client: httpx.Client, url: str, query: str) -> dict:
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = client.post(
                url,
                data={"query": query},
                headers={
                    "Accept": "application/sparql-results+json",
                    "User-Agent": _UA,
                },
            )
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:  # network, 5xx, bad JSON
            last = e
            time.sleep(0.5 * (attempt + 1))
    raise LindasError(f"LINDAS failed after 3 attempts: {last}")


def _record(b: dict, municipality: str) -> CompanyRecord:
    def v(key: str) -> str | None:
        return b[key]["value"] if key in b else None

    purpose = v("desc") or ""
    return CompanyRecord(
        uid=v("uid") or "",
        name=v("name") or "",
        legal_form=v("lf") or "",
        municipality=municipality,
        street=v("street"),
        postal_code=v("plz"),
        city=v("city"),
        purpose=purpose,
        purpose_head=head_clause(purpose),
    )


def fetch_companies(
    municipality: str,
    *,
    url: str,
    page_size: int = 2000,
    client: httpx.Client | None = None,
) -> list[CompanyRecord]:
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))
    try:
        records: list[CompanyRecord] = []
        offset = 0
        while True:
            data = _post(client, url, build_query(municipality, page_size, offset))
            bindings = data.get("results", {}).get("bindings", [])
            records.extend(_record(b, municipality) for b in bindings)
            if len(bindings) < page_size:
                break
            offset += page_size
    finally:
        if own:
            client.close()
    if not records:
        raise LindasError(
            f"LINDAS returned no AG/GmbH companies for municipality {municipality}"
        )
    return records
