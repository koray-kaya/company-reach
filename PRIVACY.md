# Privacy

company-reach finds people at Swiss companies and drafts an invitation for a
human to send. The people it finds are real, and under the Swiss Federal Act
on Data Protection (revDSG) their names and addresses are personal data even
when a website or the commercial register publishes them. This page says
what the tool collects, where it keeps it, where it sends it, and how it is
deleted. It describes the tool; whoever runs it is responsible for how they
use it.

## What it collects

- **Register data** — company name, legal form, seat, purpose, from the
  public commercial register (Zefix, via LINDAS).
- **Register notices** — the people named in a company's notices in the
  Swiss Official Gazette of Commerce (SHAB), with their role and the notice
  date. Asked for only when the company's website names nobody.
- **Company websites** — the text of up to ten pages of the company's own
  site, and from it the people named there, their roles and any addresses
  the pages publish.
- **What the tool writes** — a short description of each company, the
  contact it chose and why, an invitation draft, and the reviewer's decision
  (sent, skipped, never again).

It does not collect anything from LinkedIn. When no address was found, a
web search restricted to LinkedIn profiles may record a profile *URL* as a
lead for a human to check; LinkedIn itself is never requested.

## Where it keeps it

Only on the machine that runs it, under `data/`:

| Place | What |
|---|---|
| `data/company_reach.db` (SQLite) | companies, scores, site evidence, the search log (each query, and SearXNG's first ten result URLs — never Brave's), page text, profiles, contacts, drafts, results, the ledger of decisions, the never-again list |
| `data/cache/` | the HTML of every page fetched |
| `data/runs/<run>/manifest.json` | each run's settings, prompt versions and counts — no personal data |

`data/` is excluded from git, and CI fails if any file under it ever appears
in the repository's history. The Docker image contains the code only;
`data/`, `.env` and `profile.toml` are mounted when the container runs.

## Where it sends it

| Recipient | What it receives | When |
|---|---|---|
| **The language model endpoint** you configure (`LLM_BASE_URL`) | register purposes (scoring); the text of candidate and company pages, which can contain names and addresses (site choice, page choice, extraction); the company description, the contact's role and your `about_me` (drafting) | every run |
| **Search engines**, through your SearXNG instance | company names, UIDs and addresses; for a LinkedIn lead, a person's name with the company name | finding a site; a lead |
| **Brave Search API**, only if you set `BRAVE_SEARCH_API_KEY` | company names with their seat, and UIDs — never a person's name: the LinkedIn lead search goes to SearXNG only | only when SearXNG fails, when it answered nothing, and before a company is recorded as having no website. Brave's results are not stored: for a Brave query the tool keeps the query, the number of results and any error, and a candidate site only Brave found is never written down |
| **Company websites** | ordinary page requests, identified by the tool's user agent and a link to this repository | reading a site |
| **LINDAS and SHAB** | a municipality number or a company's UID | building the pool; SHAB only when a site names nobody |

Choose the model endpoint with that list in mind: it is a third party
processing the page text and names the tool reads. Tracing
(`LANGSMITH_TRACING`) is off by default and should stay off — a trace would
contain the same page text and names and send them to one more party.

**Nothing is sent by e-mail.** "Send" on the review page records the
decision and opens the draft in your own mail program; the mail leaves only
when you press send there. The review page is reachable on `127.0.0.1`
only — directly, or published there by Docker — and refuses any decision
that did not come from the page itself.

## What the invitation tells its reader

Every draft carries a fixed sentence, added by code rather than written by
the model, telling the reader where their name and address came from (the
company's website or SHAB), that they are used only for this invitation, and
that a short reply has them deleted and stops any further mail.

## How it is deleted

- **On request:** `company-reach forget <uid|email>` removes the person
  from the database and the page cache — contacts and everyone named beside
  them, drafts, profiles, site evidence, the search log, the name in the
  recommendation — and vacuums the database so nothing stays in free pages.
  The company and the address go on the never-again list, so they are never
  contacted again. The ledger keeps the decision without the address. Files
  the tool did not write are not edited; any that still name the person are
  listed.
- **After a year:** `company-reach purge --older-than 365` removes the same
  data for every company nobody has touched for that long. It keeps the
  ledger and the never-again list, including the address of a mail that was
  sent, because "contacted once, ever" rests on them. `company-reach doctor`
  reports how many companies a purge would clear.

## Before the first real invitation

Send stays locked on the review page until `SENDING_APPROVED=true` is set in
`.env`. It is there for the research this tool was built for, which needs
ethics approval before anyone is contacted; set it only once whatever
approval your use needs has been given. Send is also locked while the draft's
survey link is a placeholder.

The register data is used under the terms of its publishers; see
[NOTICE](NOTICE).
