# Application shell — research and recommendations

Date: 2026-09-19. Scope: the shell around the pipeline (layout, CLI, storage,
review UI, config, Docker Compose, logging). Not the pipeline steps themselves.
Versions below were read from the PyPI JSON API on 2026-09-19.

## 1. Layout and tooling

**Recommendation:** `uv init --package company-reach` → src layout, one
`pyproject.toml`, `uv.lock` committed, `ruff` + `pytest` in the `dev` group.
Python **3.13**.

- `uv init --package` creates `.python-version`, `pyproject.toml`,
  `src/company_reach/__init__.py` and a `[project.scripts]` entry
  (`company-reach = "company_reach:main"`) with `uv_build` as backend
  ([uv: init](https://docs.astral.sh/uv/concepts/projects/init/)). The `dev`
  group is synced by default and excluded with `--no-dev`
  ([uv: dependencies](https://docs.astral.sh/uv/concepts/projects/dependencies/)).
- Ruff minimal config: `[tool.ruff] line-length = 88`,
  `[tool.ruff.lint] select = ["E","F","I","UP","B"]`; commands `ruff check`,
  `ruff format` ([ruff docs](https://docs.astral.sh/ruff/configuration/)).
- **Python 3.13, not 3.12.** Per the devguide, 3.12 is in *security* mode (no
  more binaries), 3.13 and 3.14 are in *bugfix*, 3.15 lands 2026-10-01
  ([devguide](https://devguide.python.org/versions/)). Library check:
  Playwright 1.63.0 (2026-09-15) is a `py3` wheel, `>=3.10`, classifiers to
  3.14; Crawl4AI 0.9.3 (2026-08-31) `>=3.10`, classifiers to **3.13** only, and
  it pins `lxml<7`, `numpy<3`, `unclecode-litellm==1.81.13` (heavy); lxml
  6.1.3 (2026-09-02) ships `cp313` and `cp314` wheels for macOS arm64 and
  manylinux. So 3.13 is the newest version every candidate library declares.
  Note: Playwright's official image is still `python:3.12-bookworm` based
  ([playwright docker](https://playwright.dev/python/docs/docker)); if a
  browser is ever needed in Docker, install it with
  `playwright install --with-deps chromium` on our own image rather than
  switching base images. Start without Playwright/Crawl4AI: static fetch +
  lxml covers most sites (LEARNINGS §4), and Crawl4AI's dependency tree is
  larger than the rest of the tool combined.

Dockerfile, following the official uv guide
([uv: docker](https://docs.astral.sh/uv/guides/integration/docker/)); the
guide lists `python3.13-trixie-slim` (no bookworm variant for 3.13) and
advises pinning the uv version:

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["company-reach"]
CMD ["review"]
```

## 2. CLI

| | typer 0.27.2 (2026-08-28) | click 8.5.0 (2026-08-26) | argparse |
|---|---|---|---|
| deps | rich, shellingham, annotated-doc; Click is vendored since 0.26 | none | stdlib |
| style | function signature = interface | decorators per option | manual parser tree |

**Recommendation: typer.** For six subcommands with a handful of options,
the signature-is-the-interface style is the easiest to read for someone
learning Python, `--help` is generated, and the wiring to
`[project.scripts]` is one line. Cost: `rich` comes along. Click is the
zero-dependency fallback if that bothers us; argparse only if we want no
third-party CLI code at all.
Sources: [typer](https://typer.tiangolo.com/), PyPI JSON for typer/click.

```python
# src/company_reach/cli.py
import typer
app = typer.Typer(help="Find Swiss companies, find the person, draft the mail.")

@app.command()
def pool(municipality: str = "3203") -> None:      # step 1: LINDAS → companies
    ...

@app.command()
def select(seed: int, size: int = 20) -> None:      # step 2: LLM scoring, recorded seed
    ...

@app.command()
def review(port: int = 8000) -> None:               # step 6: local web UI
    ...
# pyproject: [project.scripts] company-reach = "company_reach.cli:app"
```

## 3. Storage

**Recommendation: stdlib `sqlite3`, one `data/company_reach.db`, schema in a
`schema.sql` file run with `executescript()`.** Large artefacts (raw HTML,
run manifests) go to files under `data/`.

- Why not plain files only: the ledger's rules — *a company is contacted once,
  ever*; suppression is permanent; no company is redrawn — are `UNIQUE` and
  `NOT EXISTS` in SQL, and the review UI needs "next undecided candidate of
  run X". With JSON-per-company, every one of those becomes hand-written
  loops. JSON files stay useful as export and for the page cache.
- Why not an ORM: sqlmodel 0.0.42 is still "Development Status :: 4 - Beta"
  and pins `SQLAlchemy<2.1`; peewee 4.5.1 is small (zero deps) and fine, but
  neither is needed at hundreds of rows, and writing the SQL is the thing to
  learn. Revisit peewee if the query code grows past a few hundred lines.
- `sqlite3` specifics (Python docs): open with `autocommit=False` (3.12+, the
  PEP 249 behaviour; the legacy default will change later), set
  `row_factory = sqlite3.Row`, use `:named` placeholders, and remember the
  `with con:` block commits/rolls back but does not close
  ([sqlite3 docs](https://docs.python.org/3/library/sqlite3.html)).

Minimal schema (one row per fact, every row carries the `run_id` that made it):

```sql
CREATE TABLE companies (uid TEXT PRIMARY KEY, name TEXT, legal_form TEXT,
  municipality TEXT, address TEXT, purpose TEXT, purpose_head TEXT,
  imported_at TEXT, pool_run_id TEXT);
CREATE TABLE runs (id TEXT PRIMARY KEY, stage TEXT, started_at TEXT, finished_at TEXT,
  seed INTEGER, model TEXT, prompt_versions TEXT /*json*/, settings TEXT /*json*/,
  counts TEXT /*json*/, git_commit TEXT);
CREATE TABLE candidates (run_id TEXT, uid TEXT, rank INTEGER, score REAL,
  verdict TEXT /*keep|drop*/, reason TEXT, PRIMARY KEY (run_id, uid));
CREATE TABLE searches (id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT, query TEXT,
  engine TEXT, results TEXT /*json*/, chosen_url TEXT, evidence TEXT);
CREATE TABLE pages (url TEXT PRIMARY KEY, fetched_at TEXT, http_status INTEGER,
  text TEXT, raw_path TEXT, run_id TEXT);                      -- page cache
CREATE TABLE contacts (id INTEGER PRIMARY KEY, uid TEXT, name TEXT, role TEXT,
  email TEXT, email_kind TEXT /*seen|constructed|generic*/, source TEXT /*site|shab*/,
  source_url TEXT, run_id TEXT);
CREATE TABLE drafts (id INTEGER PRIMARY KEY, uid TEXT, contact_id INTEGER,
  subject TEXT, body TEXT, prompt_version TEXT, model TEXT, run_id TEXT, created_at TEXT);
CREATE TABLE decisions (id INTEGER PRIMARY KEY, uid TEXT, draft_id INTEGER,
  decision TEXT /*send|skip*/, reason TEXT, decided_at TEXT);
CREATE TABLE ledger (uid TEXT PRIMARY KEY, status TEXT /*sent|skipped|replied|suppressed*/,
  draft_id INTEGER, changed_at TEXT, note TEXT);              -- PK = once, ever
CREATE TABLE suppression (key TEXT PRIMARY KEY /*uid or email*/, reason TEXT, added_at TEXT);
```

## 4. Review UI (send / skip)

| option | click recorded? | deps | Docker fit |
|---|---|---|---|
| (a) static HTML + `mailto:` | no (v0 problem) | none | file only |
| (b) FastAPI + Jinja2, plain forms | **yes** | fastapi, starlette, uvicorn, jinja2, python-multipart | port 8000, reads/writes `data/` |
| (c) Streamlit 1.64.0 | yes | 29 deps incl. pandas, numpy, pyarrow, altair, protobuf | port 8501; reruns the whole script on each click |
| (d) terminal prompts + `webbrowser.open("mailto:…")` | yes | none | breaks in Docker: the container cannot open the host's mail client |

**Recommendation: (b).** `fastapi[standard]` (0.141.1) bundles uvicorn,
jinja2 and python-multipart ([PyPI fastapi](https://pypi.org/pypi/fastapi/json)).
One template: card with recommendation up front (LEARNINGS §7), every address
with its kind, the draft, two buttons. *Skip* is a plain `<form method="post">`.
*Send* is `<a href="mailto:…">` with a three-line inline handler:
`fetch('/decide/{uid}/send', {method:'POST'}).then(() => location.reload())` —
the `mailto:` navigation opens Outlook without unloading the page, so the POST
completes and the row is written. HTMX (2.0.10, dependency-free, one script
file) is not needed for this; note it cancels the native click on anchors it
handles, so it would complicate the mailto button rather than help. Streamlit
is the wrong shape: heavy and a rerun-everything model for a queue of
decisions. (d) is a fine non-Docker fallback for debugging.

**`mailto:` rules (RFC 6068):** non-ASCII → UTF-8 → percent-encode each octet
(`ü` → `%C3%BC`); body line breaks **must** be `%0D%0A`; spaces as `%20`,
never `+` ([RFC 6068](https://www.rfc-editor.org/rfc/rfc6068)). In Python:
`urllib.parse.quote(text, safe="")` after normalising newlines to `\r\n`; do
not use `urlencode`/`quote_plus`. **Length:** no vendor publishes a mailto
limit for Outlook for Mac; the documented figures are classic Outlook/Windows
at 2,083 characters (raised to 8,192 in build 16116 for links in mail
bodies) and Gmail at ~4,096 including subject and recipients
([MS Q&A](https://learn.microsoft.com/en-us/answers/questions/1063670/outlook-for-microsoft-365-truncating-long-urls),
[Neowin](https://www.neowin.net/news/you-can-now-send-much-longer-links-with-microsoft-outlook-thanks-to-bug-fix/),
[maple #1964](https://github.com/codeforboston/maple/issues/1964)).
Rule: keep the encoded URL under **2,000 characters**; with umlauts and
`%0D%0A` a German plain-text mail of ~1,200 raw characters fits. The draft
step should measure this and warn; the page always shows the body as text to
copy as the fallback. v0 confirmed the `mailto:` → Outlook path works (LEARNINGS §6).

## 5. Configuration and secrets

`pydantic-settings` 2.15.0 (deps: pydantic, python-dotenv). Priority: init
args > environment > `.env` > defaults, so Compose `env_file:` and a local
`.env` behave the same ([pydantic-settings docs](https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/)).

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    anthropic_api_key: SecretStr
    searxng_url: str = "http://searxng:8080"
    model: str                      # exact model id set in .env, recorded in every run
    monthly_mail_cap: int = 100
    data_dir: Path = Path("data")
```

`.env.example`: committed, every key present, placeholder values
(`ANTHROPIC_API_KEY=sk-ant-...`), one comment line per key, same order as the
class. `.env` stays gitignored (already). `SecretStr` keeps the key out of
`repr` and logs.

## 6. Docker Compose

Both modes with one image: `docker compose up` runs the review UI (default
`CMD`), `docker compose run --rm app select --seed 42` runs a stage.
`compose run` overrides the command, starts dependencies, and does **not**
publish ports unless `--service-ports`
([compose run](https://docs.docker.com/reference/cli/docker/compose/run/)).
Services reach each other by service name on the container port, so the app
uses `http://searxng:8080` ([compose networking](https://docs.docker.com/compose/how-tos/networking/)).

```yaml
services:
  app:
    build: .
    env_file: .env
    ports: ["127.0.0.1:8000:8000"]
    volumes: ["./data:/app/data"]        # bind mount: files readable from the host
    depends_on: [searxng]
  searxng:
    image: docker.io/searxng/searxng:${SEARXNG_VERSION:-latest}  # pin a dated tag, format 2026.3.25-541c6c3cb
    volumes: ["./searxng:/etc/searxng"]
    # not published: only the app talks to it
```

- `searxng-docker` is superseded; the official template is
  `container/docker-compose.yml` in the searxng repo (adds a Valkey
  service, which a single local user can drop)
  ([searxng install](https://docs.searxng.org/admin/installation-docker.html)).
  In `searxng/settings.yml`: `search.formats: [html, json]` for
  `?format=json`, and `server.limiter: false` since it is not exposed
  ([settings](https://docs.searxng.org/admin/settings/settings_search.html),
  [limiter](https://docs.searxng.org/admin/searx.limiter.html)).
- Bind mount over named volume for `data/`: the user reads the SQLite file and
  the JSON exports from the host; Compose docs reserve named volumes for
  engine-managed stores ([compose volumes](https://docs.docker.com/reference/compose-file/volumes/)).
  A named volume (`data:/app/data` + top-level `volumes: {data: {}}`) works
  the same if host access is not wanted.

## 7. Logging and reproducibility

**Recommendation: stdlib `logging` now; run manifest as JSON.**
`logging.basicConfig(level=INFO, format=...)` to stderr plus a `FileHandler`
at `data/runs/<run_id>/run.log`; modules use `logging.getLogger(__name__)`
([logging docs](https://docs.python.org/3/library/logging.html)). structlog
26.1.0 (zero deps on 3.11+) gives bound context and JSON lines and wraps
stdlib; worth it once logs are read by machines, not before
([structlog](https://www.structlog.org/en/stable/why.html)).

The reproducibility artefact is the **run manifest**, written twice (start,
end) to `data/runs/<run_id>/manifest.json` and mirrored in the `runs` table:
`{"id","stage","started_at","finished_at","seed","model","prompt_versions":
{"select":"select_v3.md@sha256:…"},"settings":{…non-secret…},
"counts":{"in":…,"kept":…,"dropped":…},"git_commit":…}`. Prompts live in
`prompts/<stage>_v<N>.md`; the version string is filename plus content hash,
so an edited file without a bump is still detectable. Every LLM call logs
`run_id, uid, prompt_version, model, input_hash` and stores the raw response
next to the manifest.
