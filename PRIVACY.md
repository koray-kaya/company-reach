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
  (sent, skipped, never again, and a send taken back as not sent or
  bounced).

It does not collect anything from LinkedIn. When no address was found, a
web search restricted to LinkedIn profiles may record a profile *URL* as a
lead for a human to check; LinkedIn itself is never requested.

## Where it keeps it

Only on the machine that runs it, under `data/`:

| Place | What |
|---|---|
| `data/company_reach.db` (SQLite) | companies, scores, site evidence, the search log (each query, and SearXNG's first ten result URLs — never Brave's), page text, profiles, contacts, drafts, results, the ledger of decisions (for a mail sent: the address, the subject and a hash of the text), the never-again list, and — once imported from the survey's export — when each company's response started and finished (by UID; `report` prints only counts) |
| `data/cache/` | the HTML of every page fetched |
| `data/runs/<run>/manifest.json` | each run's settings, prompt versions and counts, and your own goal and `about_me` — nobody else's personal data |

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

The model writes one sentence of the invitation, saying why this company
gets it. Everything else is written by code (`tools/invitation.py`):

- **Who writes.** The sender's name and school open the mail, and the
  signature repeats them with the programme and, only with their consent,
  the supervisor. Together with the From address this names who is
  responsible for the data.
- **Where the name and address came from, and what for.** One fixed text
  per kind of contact, never the model's words:

  | Contact | Text |
  |---|---|
  | named on the site, their own address | Ihren Namen und Ihre Adresse habe ich von Ihrer Website und nutze beides nur für diese Anfrage. |
  | named on the site, the inbox the site publishes | Ihren Namen und diese Adresse habe ich von Ihrer Website und nutze beides nur für diese Anfrage. |
  | named on the site, info@ guessed | Ihren Namen habe ich von Ihrer Website und nutze ihn nur für diese Anfrage. |
  | named in SHAB, the inbox the site publishes | Ihren Namen habe ich aus dem Handelsamtsblatt (SHAB), diese Adresse von Ihrer Website; ich nutze beides nur für diese Anfrage. |
  | named in SHAB, info@ guessed | Ihren Namen habe ich aus dem Handelsamtsblatt (SHAB) und nutze ihn nur für diese Anfrage. |
  | named in SHAB, their own address on the site | Ihren Namen habe ich aus dem Handelsamtsblatt (SHAB), Ihre Adresse von Ihrer Website; ich nutze beides nur für diese Anfrage. |
  | nobody named, the inbox the site publishes | Diese Adresse habe ich von Ihrer Website und nutze sie nur für diese Anfrage. |

  SHAB is never given as the source of an address, and a guessed address
  is not said to come from anywhere.
- **One mail only.** "Ich schreibe Ihnen nur dieses eine Mal." The ledger
  keeps that promise, for the company and for the inbox: the review page
  refuses an address that is on the never-again list or was already written
  to for another company, such as a sister firm sharing one info@. A mail
  nobody received is not a contact: one that never left the mail client is
  taken back on the page, and one that bounced puts its address on the
  never-again list (a bounce clicked by mistake is undone before anything
  else is decided); either way the company may be written to at another
  address. Only if the profile allows one reminder (which needs ethics
  approval first) does the mail say "Ich erinnere Sie höchstens einmal
  daran" instead.
- **How to be deleted.** When someone is named: "Ein kurzes «Nein» genügt,
  dann lösche ich Ihren Namen." It promises the name, not the address,
  because `forget` keeps the address on the never-again list for good.
- **What the link carries.** "Der Link enthält die UID Ihrer Firma;
  veröffentlicht werden nur zusammengefasste Ergebnisse." The survey's first
  page carries the full notice. When the length experiment is on
  (`[invitation] experiment`), half the companies — chosen by their UID — get
  the short mail, which leaves out this line and the results offer; for them
  the survey's first page is where they read that the link carries the UID.
  That page must say so before the experiment is switched on.

## How it is deleted

- **On request:** `company-reach forget <uid|email|survey link>` removes the person
  from the database and the page cache — contacts and everyone named beside
  them, drafts, profiles, site evidence, the search log, the imported survey
  response times for the company, the name in the recommendation — and
  vacuums the database so nothing stays in free pages.
  The company and the address go on the never-again list, so they are never
  contacted again. The ledger keeps the decision without the address or
  the mail's subject; the address a mail went to moves to the never-again
  list instead. An address
  is looked up in the contacts, the persons a profile names and the ledger.
  When no company holds it, `forget` still puts it on the never-again list,
  says that nothing was deleted, and exits with status 2: the company is
  found by the UID in the survey link the reply quotes (`?c=CHE…`), and
  `forget <UID>`, or `forget` with the pasted link, deletes it. A UID
  whose check digit does not match is refused, and one no table holds is
  handled like an unknown address: listed, nothing deleted, status 2.
  Files the tool did not write are not edited;
  any that still name the person are listed — including a copy of the
  database (a backup under `data/`, with its `-wal` file) and a log, which
  have to be deleted or cleaned by hand. A file it cannot search — a
  compressed archive, a PDF, one it may not read — is listed as not
  searched.
- **After a year:** `company-reach purge --older-than 365` removes the same
  data for every company nobody has touched for that long — one a run drew,
  and one only `enrich --uid` or an evaluation looked at, whose age is its
  newest search or draft. It keeps the
  ledger and the never-again list, including the address of a mail that was
  sent, because "contacted once, ever" rests on them; only the mail's
  subject, which can name the person, is cleared. `company-reach doctor`
  reports how many companies a purge would clear.

## Before the first real invitation

Send stays locked on the review page until `SENDING_APPROVED=true` is set in
`.env`. It is there for the research this tool was built for, which needs
ethics approval before anyone is contacted; set it only once whatever
approval your use needs has been given. Send is also locked while the draft's
survey link is a placeholder.

The register data is used under the terms of its publishers; see
[NOTICE](NOTICE).
