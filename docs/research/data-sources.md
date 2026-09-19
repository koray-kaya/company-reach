# Data sources: LINDAS and SHAB

Verified with live HTTP calls on 2026-09-19. Everything below was observed,
not recalled. Person names are replaced with `<Name>`.

## A. LINDAS (Swiss register as linked data)

Endpoint `https://lindas.admin.ch/query`, graph `https://lindas.admin.ch/foj/zefix`.
POST form field `query`, header `Accept: application/sparql-results+json`
(`text/csv` also works). GET works too. No key, no rate-limit headers.

### A1. Result shape and available predicates

The v0 query (LEARNINGS §1, `admin:municipality <https://ld.admin.ch/municipality/3203>`,
legal forms `0106`/`0107`) with `ORDER BY ?uid LIMIT 5` returns in ~2 s:

```json
{"head":{"vars":["c","uid","name","lf","desc"]},
 "results":{"bindings":[{"c":{"type":"uri","value":"https://register.ld.admin.ch/zefix/company/<id>"},
   "uid":{"type":"literal","value":"CHE000000001"},"name":{"type":"literal","value":"… AG"},
   "lf":{"type":"literal","value":"0106"},"desc":{"type":"literal","value":"Beteiligung an …"}}, …]}}
```

Predicate census over all 793,561 `admin:ZefixOrganisation` in the graph (46 s query):
there are exactly ten predicates on an organisation, nothing else.

| predicate | note |
|---|---|
| `schema:legalName` | registered name, one per company (8,453 in 3203) |
| `schema:name` | legalName plus translated names with `@en`/`@fr` tags (e.g. `… Ltd`, `… SA`) — 9,128 values for 8,453 companies; filter `LANG(?n)=""` or use legalName |
| `schema:description` | purpose text; **missing on 267 of 8,453** (3.2%) — use OPTIONAL |
| `schema:additionalType` | legal form IRI `https://ld.admin.ch/ech/97/legalforms/0106`; labels via `schema:name` on that IRI (`Aktiengesellschaft` / `Gesellschaft mit beschränkter Haftung GMBH / SARL`) |
| `schema:identifier` | three `schema:PropertyValue` nodes: `schema:name` = `CompanyUID` (value `CHE000000002`, no dots), `CompanyCHID` (`CH020…`), `CompanyEHRAID` (numeric) — filter on `schema:name "CompanyUID"` rather than `STRSTARTS("CHE")` |
| `schema:address` = `locn:address` | one `schema:PostalAddress` node with `schema:streetAddress`, `schema:postalCode`, `schema:addressLocality`, `schema:addressRegion` (canton code) and locn duplicates (`locn:thoroughfare`, `locn:locatorDesignator` = house number, `locn:postCode`, `locn:postName`) |
| `admin:municipality` | FSO municipality IRI |
| `schema:inDefinedTermSet`, `rdf:type` | constant |

**Not in the graph:** status, deletion, liquidation flag, registration date, seat
history, persons, website. The dataset description states it holds *active*
legal entities only, refreshed daily (`schema:dateModified` 2026-09-18).
"in Liquidation" is only visible as a name suffix: 357 of 8,453 (4.2%) in 3203.
Deleted companies simply disappear on the next daily load.

### A2. Paging, caps, municipality lookups

- No server-side cap was hit: the full 3203 AG+GmbH set (5,465 rows, 6.3 MB
  JSON) came back in one response in 2.8 s; `OFFSET 5000 LIMIT 100` with
  `ORDER BY ?uid` works (2.2 s). A 46 s aggregate over the whole graph did not
  time out. For a single municipality, one request without LIMIT is fine;
  for a canton, page with `ORDER BY ?uid LIMIT 2000 OFFSET n`.
- Municipality IRIs live in graph `https://lindas.admin.ch/fso/register`.
  Lookup by name (verified):

  ```sparql
  PREFIX schema: <http://schema.org/>  PREFIX admin: <https://schema.ld.admin.ch/>
  SELECT ?m ?name WHERE { GRAPH <https://lindas.admin.ch/fso/register> {
    ?m a admin:PoliticalMunicipality ; schema:name ?name .
    FILTER(?name IN ("Gossau (SG)","Wil (SG)","Rapperswil-Jona"))
    FILTER NOT EXISTS { ?m a <https://version.link/Deprecated> } } }
  ```
  → `municipality/3443` Gossau (SG), `municipality/3427` Wil (SG),
  `municipality/3340` Rapperswil-Jona. The `Deprecated` filter matters: "Wil (SG)"
  also matches the pre-merger IRI `3425` (0 companies), and `/municipality/version/…`
  IRIs match without the class filter.
- All municipalities of canton SG: same pattern with
  `?m schema:containedInPlace <https://ld.admin.ch/canton/17>` → 75 rows
  (SG has 75 municipalities). Adding
  `OPTIONAL { GRAPH <…/foj/zefix> { ?c a admin:ZefixOrganisation ; admin:municipality ?m } }`
  and `COUNT(?c)` gives the pool size per municipality (St. Gallen 8,453,
  Rapperswil-Jona 2,461, Wil 2,147, Gossau 1,444, Buchs 1,325, …).

### A3. Terms

- LINDAS site: no rate limit or quota is published. `/sparql/` says only
  "Before running larger or automated queries, please check the LINDAS
  knowledge base for efficient SPARQL usage and fair use guidance"; that
  knowledge base (`cognizone.atlassian.net/wiki/external/…`) needs JavaScript
  and could not be read here. Footer "Terms and conditions" points to the
  generic `https://www.admin.ch/gov/en/start/terms-and-conditions.html`
  (federal copyright notice; it does not govern the dataset).
- Dataset metadata inside the graph (`https://register.ld.admin.ch/.well-known/dataset/foj-zefix`):
  `dcterms:rights https://ld.admin.ch/vocabulary/TermsOfUse/Provide-the-Source`
  = "Open use. **Must** provide the source" (identifier
  `NonCommercialAllowed-CommercialAllowed-ReferenceRequired`, opendata.swiss `OPEN BY`).
  opendata.swiss (`https://opendata.swiss/de/dataset/zefix-zentraler-firmenindex`,
  publisher EHRA) lists resources under both `terms_open` and `terms_by`.
  **Contradicts LEARNINGS §1** ("attribution recommended"): treat attribution
  to Zefix/EHRA as required.

### A4. Python client

Use plain `httpx` (`httpx.post(url, data={"query": q}, headers={"Accept": "application/sparql-results+json"}, timeout=120)`).
`SPARQLWrapper` adds nothing here: one endpoint, one result format, no auth.
Neither library is installed on this machine yet (Python 3.14.7).

## B. SHAB (Swiss Official Gazette of Commerce)

Base `https://www.shab.ch/api/v1/publications` (same API as
`https://amtsblattportal.ch/api/v1/publications`; official docs:
`https://amtsblattportal.ch/docs/api/`, section 3.4 lists every parameter).

### B1. List call and response shape

`GET …/publications?publicationStates=PUBLISHED&rubrics=HR&cantons=SG&publicationDate.start=2026-08-20&publicationDate.end=2026-09-19&pageRequest.size=100`
→ 1,122 publications in 30 days (110,764 for HR/SG all-time since 2018).
**`publicationStates=PUBLISHED` is mandatory: without it the call returns
`401 Access is denied`.** Response: `{"content":[…],"total":1122,"pageRequest":{"page":0,"size":100,"sortOrders":[]}}`.
Each item is `{"meta":{…},"links":[],"attachments":[],"content":null,"commented":false}`;
`meta` carries `id`, `rubric`, `subRubric` (`HR01` new entry, `HR02` mutation,
`HR03` deletion), `language`, `publicationDate`, `publicationNumber`,
`cantons`, `title.{de,en,fr,it}`, `registrationOffice`. **The list never
contains the text** — a second call per publication is needed:

- `GET …/publications/{id}/xml` → `<HR01:publication>` with `<meta>` and
  `<content>`: `journalDate`, `publicationText` (the full notice as one
  string), plus for HR01 structured `company/name`, `uid`, `address`, `purpose`, `capital`.
- `GET …/publications/{id}` (JSON) → richer meta (`uid:["CHE-000.000.003"]`,
  `municipalities`, `hasSignedPdf`) but **no text**. `/pdf` returns the signed PDF.
- XML list: `GET …/publications/xml?…` returns `<bulk:bulk-export>` with the
  same meta and a `ref` attribute pointing at each `/xml` resource.
  `Accept: application/xml` on the JSON endpoints also works.

Paging: `pageRequest.page` (0-based) and `pageRequest.size` (default 100,
documented max 2,000; 2,000 verified). Sorting works only in the documented
syntax `pageRequest.sortOrders=column:PUBLICATION_DATE|direction:ASC`
(`|` URL-encoded as `%7C`; columns `PUBLICATION_DATE`, `UPDATE_DATE`,
`COPY_DEADLINE`, `PUBLICATION_NUMBER`). Default is date descending.

### B2. Filters: what is ignored, what works

- **Unknown parameters are silently ignored**, including `uid=` (the v0
  pitfall — total stays 110,764), `q`, `text`, `searchText`, `fullText`.
  Spring-style `sortOrders[0].x` is rejected with 400.
- **`uids=CHE-000.000.003` works** (plural, dotted format only; `CHE000000003`
  → 0 hits). Repeatable: `uids=A&uids=B`. A 2003 company returned all 24 of
  its publications back to 2018-12 — this is the right way to fetch a company's history.
- `keyword=` works on the text (whole words, `*` wildcard): `keyword=CHE-000.000.003`
  → 1 hit, a name fragment → 2 hits. `title=` searches the title only.
- `subRubrics=HR02` works without `rubrics`; when both are given the
  sub-rubric is ignored in favour of the rubric (677 vs 470 in 19 days).
- Always assert that a filter changed `total` before trusting it.

### B3. Person blocks

German (federal office, all SG HR notices; 26 of 40 September HR02 mutations had a block):

```
… CHE-000.000.004, Aktiengesellschaft (SHAB Nr. <n> vom <date>, Publ. <number>).
Ausgeschiedene Personen und erloschene Unterschriften: <Name>, von <Heimatort>, in <Wohnort>,
Mitglied des Verwaltungsrates, mit Kollektivunterschrift zu zweien; <Name>, von <Heimatort>,
in <Wohnort>, Mitglied des Verwaltungsrates, mit Kollektivunterschrift zu zweien.
Eingetragene Personen neu oder mutierend: <Name>, von <Heimatort>, in <Wohnort>,
mit Kollektivprokura zu zweien.
```
HR01 uses `Eingetragene Personen:`; entries are `Surname, Firstname, [citizenship
/ von Heimatort], in Wohnort, role[ und role], signature[, Stammanteile]`,
separated by `;`. Roles seen (40 notices): Mitglied / Präsident des
Verwaltungsrates, Geschäftsführer(in), Vorsitzende(r) der Geschäftsführung,
Gesellschafter(in), Liquidator(in), Revisionsstelle (a company, not a person),
Prokura-only entries with no role.

French: the format depends on the cantonal registry. FR uses headers
(`Personne radiée:`, `Personnes radiées:`, `Personne inscrite modifiée:`);
VD, GE and NE write prose with no header:

```
<Company> Sàrl, à <lieu>, CHE-…, (FOSC du <date>, p. 0/…). Les 50 parts de CHF 100 de
<Name>, qui n'est plus associé-gérant et dont la signature est radiée, sont cédées à <Name>,
de <lieu d'origine>, à <domicile>, nouvel associé-gérant avec signature individuelle, …
```
```
… Sa liquidation est opérée sous la raison sociale: <Company> Sàrl, en liquidation.
Liquidatrice: l'associée-gérante <Name>, laquelle continue à signer individuellement.
```
French roles seen: administrateur/administratrice (unique, président(e),
vice-président(e)), associé(e)-gérant(e), gérant(e), membre, liquidateur/-trice,
organe de révision, directeur/-trice.

Parsing approach: (1) split the text at the German headers
`(Eingetragene Personen[^:]*:|Ausgeschiedene Personen[^:]*:)` or the FR
headers `Personnes? (inscrite|radiée)s?[^:]*:`; (2) inside a block split on
`; `, then on `, ` — fields 0–1 are surname and first name, the last field
starting with `mit ` / `avec ` / `ohne ` is the signature, the role is the
field matching a role vocabulary, the rest is origin/residence; (3) for VD/GE/NE
prose, match `(?P<name>[^,]+), (?:de [^,]+, )?(?:à [^,]+, )?(?P<role>nouve(?:l|lle|au) [\w\- ]+|n'est plus [\w\- ]+)`
as a lead only. Mark "Ausgeschiedene"/"radiée"/"n'est plus" entries as
departed, never as current. Keep the notice date: SHAB names a past state.

### B4. Terms of use (found)

shab.ch is a single-page app; its footer "Datenschutzerklärung" links to
`https://www.shab.ch/#!/search/info/privacy-policy` (text is served in
`GET https://www.shab.ch/api/v1/tenants`, tenant `shab`, `textBlocks`). The
binding terms are the **Amtsblattportal AGB of 07.06.2024**
(`https://amtsblattportal.ch/static/agb_de.pdf`, issued by SECO under Art. 13 VSHAB), section 3:
- 3.1/3.2: use and exploitation of the data is allowed for personal **and
  commercial** purposes; an API is provided for transferring publication data.
- 3.3 obligations: comply with Swiss data-protection law when processing
  personal data; for *publishing* personal data Art. 11 para. 3 VSHAB applies
  by analogy; **cite the source (SHAB)**; do not create the impression of an
  official publication (suggested wording: "Dies ist keine amtliche
  Veröffentlichung. Massgebend sind die vom SECO … signierten Daten im
  Amtsblattportal"); show unchanged data distinct from own comments.
- 6: violations can lead to an IP block of up to a year.
- The API docs (`/docs/api/` §1.1): "The API is freely accessible for anyone
  to use … designed for productive use", completeness not guaranteed. No
  rate limit is documented anywhere. `/api/v1/configurations` shows the UI's
  own export cap (`maxPublicationCountToExport: 3000`).

This closes the LEARNINGS open question "SHAB's terms of use were never found".
