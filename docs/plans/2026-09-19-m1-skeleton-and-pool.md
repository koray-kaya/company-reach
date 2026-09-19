# M1 — Skeleton and Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A runnable `company-reach` CLI that pulls a municipality's AG+GmbH companies from LINDAS into SQLite and screens them with the v0 rules.

**Architecture:** `uv` project with a `src/` layout. `settings.py` reads `.env`; `tools/db.py` opens one SQLite connection per call over `schema.sql`; `tools/lindas.py` runs one paged SPARQL query with `httpx`; `screen.py` holds the head-clause split and the exclusion rules; `cli.py` exposes `pool` and `screen` with `typer`. No LangGraph yet — that is M3.

**Tech Stack:** Python 3.13, uv, typer, pydantic + pydantic-settings, httpx, sqlite3 (stdlib), pytest, respx, ruff.

**Spec:** `docs/design/2026-09-19-company-reach-design.md` §§ 4, 6, 8, 10, 11; `docs/research/data-sources.md` §A (LINDAS); `LEARNINGS.md` §§ 1–2.

## Global Constraints

- Python `>=3.13`; `uv.lock` committed; `ruff` line length 88, rules `E,F,I,UP,B`.
- Company data only under `data/` (gitignored). Test fixtures use fictional companies.
- DB access: one connection per call, `PRAGMA journal_mode=WAL`, `busy_timeout=5000`, `autocommit=False`, `with conn:` transactions (design §8).
- LINDAS: attribution required; user agent `company-reach/<version>`; `schema:description` is OPTIONAL (missing on 3.2%).
- Head clause: split the purpose on `Die Gesellschaft kann` / `Sie kann` (LEARNINGS §2).
- Files English; explanations for Koray in `docs/milestones/m1-skeleton-and-pool.md` at the end.

---

## File structure

| File | Responsibility |
|---|---|
| `pyproject.toml`, `.python-version`, `uv.lock` | project, dependencies, script entry `company-reach = "company_reach.cli:app"` |
| `.env.example` | every setting with a placeholder |
| `src/company_reach/__init__.py` | version string |
| `src/company_reach/settings.py` | `Settings` (pydantic-settings) and `get_settings()` |
| `src/company_reach/models.py` | `CompanyRecord` |
| `src/company_reach/schema.sql` | the full schema from design §8 |
| `src/company_reach/tools/db.py` | `connect()` context manager, `init_db()` |
| `src/company_reach/tools/lindas.py` | `build_query()`, `fetch_companies()` |
| `src/company_reach/screen.py` | `head_clause()`, `screen_reason()` |
| `src/company_reach/nodes/load_pool.py` | `load_pool(municipality, run_id) -> int` (plain function; becomes a node in M3) |
| `src/company_reach/nodes/screen_pool.py` | `screen_pool(run_id) -> tuple[int, int]` |
| `src/company_reach/cli.py` | `pool`, `screen` |
| `tests/conftest.py` | temp `data_dir`, settings override |
| `tests/fixtures/lindas_page.json` | one fictional SPARQL result page |
| `tests/test_*.py` | one per module |

---

### Task 1: Project skeleton and settings

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.env.example`, `src/company_reach/__init__.py`, `src/company_reach/settings.py`
- Test: `tests/test_settings.py`, `tests/conftest.py`

**Interfaces:**
- Produces: `Settings` with fields `data_dir: Path`, `lindas_url: str`, `llm_base_url`, `llm_api_key: SecretStr`, `llm_model`, `llm_structured_method`, `llm_concurrency`, `searxng_url`, `serper_api_key`, `playwright_url`, `max_pages_per_site`, `max_chars_per_page`, `score_batch_size`, `max_batches_per_run`, `langsmith_tracing`; `get_settings() -> Settings`.

- [ ] **Step 1: Create the project**

```bash
cd company-reach
uv init --package --python 3.13 --name company-reach --no-readme .
uv add typer pydantic pydantic-settings httpx
uv add --dev pytest pytest-asyncio respx ruff
```

Edit `pyproject.toml` so it contains:

```toml
[project]
name = "company-reach"
version = "0.1.0"
description = "Find Swiss companies worth talking to and draft a personal invitation for a human to send."
requires-python = ">=3.13"
dependencies = ["typer>=0.27", "pydantic>=2", "pydantic-settings>=2.15", "httpx>=0.28"]

[project.scripts]
company-reach = "company_reach.cli:app"

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=1", "respx>=0.22", "ruff>=0.14"]

[tool.ruff]
line-length = 88
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Write the failing settings test**

`tests/conftest.py`:

```python
from pathlib import Path

import pytest

from company_reach.settings import Settings


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings pointing at a temporary data dir; no .env is read."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    return Settings(_env_file=None, data_dir=tmp_path / "data")
```

`tests/test_settings.py`:

```python
from pathlib import Path

from company_reach.settings import Settings


def test_defaults_and_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LLM_API_KEY", "abc")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("MAX_PAGES_PER_SITE", "12")
    s = Settings(_env_file=None, data_dir=tmp_path)
    assert s.max_pages_per_site == 12
    assert s.score_batch_size == 50
    assert s.llm_structured_method == "json_schema"
    assert "abc" not in repr(s)  # SecretStr keeps the key out of logs
    assert s.db_path == tmp_path / "company_reach.db"
```

- [ ] **Step 3: Run it to see it fail**

Run: `uv run pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: company_reach.settings`

- [ ] **Step 4: Write settings.py**

```python
"""Typed configuration read from .env / environment.

pydantic-settings turns environment variables into one validated object, so
the rest of the code never touches os.environ. Secrets are SecretStr: they
do not appear in repr() or logs.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    lindas_url: str = "https://lindas.admin.ch/query"

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr
    llm_model: str
    llm_structured_method: Literal["json_schema", "function_calling", "json_mode"] = (
        "json_schema"
    )
    llm_concurrency: int = 5

    searxng_url: str = "http://searxng:8080"
    serper_api_key: SecretStr | None = None
    playwright_url: str | None = None

    max_pages_per_site: int = 10
    max_chars_per_page: int = 8000
    score_batch_size: int = 50
    max_batches_per_run: int = 3
    langsmith_tracing: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "company_reach.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`src/company_reach/__init__.py`:

```python
__version__ = "0.1.0"
```

`.env.example`:

```
# LLM — any OpenAI-compatible endpoint
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your-key
LLM_MODEL=your-model-id
LLM_STRUCTURED_METHOD=json_schema      # json_schema | function_calling | json_mode
LLM_CONCURRENCY=5

# Search (M4)
SEARXNG_URL=http://searxng:8080
SERPER_API_KEY=                        # optional, used only when SearXNG errors

# Optional JavaScript rendering (M5)
PLAYWRIGHT_URL=

# Limits
MAX_PAGES_PER_SITE=10
MAX_CHARS_PER_PAGE=8000
SCORE_BATCH_SIZE=50
MAX_BATCHES_PER_RUN=3

# Tracing — off by default; traces would contain names and page text
LANGSMITH_TRACING=false
```

- [ ] **Step 5: Run the test**

Run: `uv run pytest tests/test_settings.py -v`
Expected: PASS

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add pyproject.toml uv.lock .python-version .env.example src tests
git commit -m "feat: project skeleton and typed settings"
```

---

### Task 2: CompanyRecord and the head-clause rule

**Files:**
- Create: `src/company_reach/models.py`, `src/company_reach/screen.py`
- Test: `tests/test_models.py`, `tests/test_screen.py`

**Interfaces:**
- Produces: `CompanyRecord(uid, name, legal_form, municipality, street, postal_code, city, purpose, purpose_head)`; `head_clause(purpose: str) -> str`; `screen_reason(record: CompanyRecord) -> str | None` (None = keep; else the reason to drop).

- [ ] **Step 1: Write the failing tests**

`tests/test_screen.py`:

```python
from company_reach.models import CompanyRecord
from company_reach.screen import head_clause, screen_reason


def make(name="Muster AG", purpose="Betrieb einer Schreinerei.") -> CompanyRecord:
    return CompanyRecord(
        uid="CHE000000001", name=name, legal_form="0106", municipality="3203",
        street="Musterstrasse 1", postal_code="9000", city="St. Gallen",
        purpose=purpose, purpose_head=head_clause(purpose),
    )


def test_head_clause_drops_boilerplate_tail():
    p = ("Betrieb einer Schreinerei. Die Gesellschaft kann Zweigniederlassungen "
         "errichten und Grundstücke erwerben, belasten und veräussern.")
    assert head_clause(p) == "Betrieb einer Schreinerei."


def test_head_clause_sie_kann_variant():
    assert head_clause("Handel mit Möbeln. Sie kann Liegenschaften erwerben.") == "Handel mit Möbeln."


def test_head_clause_without_tail_is_unchanged():
    assert head_clause("Betrieb einer Schreinerei.") == "Betrieb einer Schreinerei."


def test_liquidation_is_dropped():
    assert screen_reason(make(name="Muster AG in Liquidation")) == "in liquidation"


def test_property_only_is_dropped():
    r = make(purpose="Erwerb, Halten und Verwaltung von Liegenschaften. Die Gesellschaft kann Zweigniederlassungen errichten.")
    assert screen_reason(r) == "property only"


def test_property_plus_operating_signal_is_kept():
    r = make(purpose="Herstellung von Fenstern sowie Verwaltung von Liegenschaften.")
    assert screen_reason(r) is None


def test_dienstleistung_alone_is_not_an_operating_signal():
    r = make(purpose="Dienstleistungen im Immobilienbereich, Verwaltung von Liegenschaften.")
    assert screen_reason(r) == "property only"
```

`tests/test_models.py`:

```python
from company_reach.models import CompanyRecord


def test_uid_is_normalised_to_digits_only():
    r = CompanyRecord(uid="CHE-000.000.042", name="X AG", legal_form="0106",
                      municipality="3203", purpose="", purpose_head="")
    assert r.uid == "CHE000000042"
    assert r.uid_dotted == "CHE-000.000.042"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_models.py tests/test_screen.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write models.py**

```python
"""Pydantic records shared by the pipeline, the database layer and (later)
the LLM output schemas. Pydantic validates on construction, so a record that
exists is a record that is well-formed."""

import re

from pydantic import BaseModel, field_validator

_UID_DIGITS = re.compile(r"\D")


class CompanyRecord(BaseModel):
    uid: str                     # normalised: CHE + 9 digits, no dots
    name: str
    legal_form: str              # "0106" AG · "0107" GmbH
    municipality: str            # FSO id, e.g. "3203"
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    purpose: str
    purpose_head: str

    @field_validator("uid")
    @classmethod
    def _normalise_uid(cls, v: str) -> str:
        digits = _UID_DIGITS.sub("", v)
        if len(digits) != 9:
            raise ValueError(f"UID must carry 9 digits: {v!r}")
        return f"CHE{digits}"

    @property
    def uid_dotted(self) -> str:
        d = self.uid[3:]
        return f"CHE-{d[:3]}.{d[3:6]}.{d[6:]}"
```

- [ ] **Step 4: Write screen.py**

```python
"""Cheap, deterministic exclusion of companies that are certainly not
operating businesses. No network, no model. Measured rules from LEARNINGS §2:
the purpose text excludes, it does not classify — so this stays conservative."""

import re

from company_reach.models import CompanyRecord

_TAIL = re.compile(r"\s*(Die Gesellschaft kann|Sie kann)\b.*", re.S)
_PROPERTY = re.compile(
    r"Liegenschaft|Immobilie|Grundstück|Grundeigentum", re.I
)
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
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_models.py tests/test_screen.py -v`
Expected: PASS (7 + 1)

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/company_reach/models.py src/company_reach/screen.py tests/test_models.py tests/test_screen.py
git commit -m "feat: company record and screening rules"
```

---

### Task 3: SQLite schema and connection helper

**Files:**
- Create: `src/company_reach/schema.sql`, `src/company_reach/tools/__init__.py`, `src/company_reach/tools/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `init_db(path: Path) -> None`; `connect(path: Path)` context manager yielding `sqlite3.Connection` with `row_factory = sqlite3.Row`; `upsert_companies(conn, records: list[CompanyRecord], run_id: str) -> int`.

- [ ] **Step 1: Write the failing test**

```python
import sqlite3
from pathlib import Path

from company_reach.models import CompanyRecord
from company_reach.tools.db import connect, init_db, upsert_companies


def rec(uid: str) -> CompanyRecord:
    return CompanyRecord(uid=uid, name=f"{uid} AG", legal_form="0106", municipality="3203",
                         purpose="Betrieb einer Schreinerei.", purpose_head="Betrieb einer Schreinerei.")


def test_init_creates_tables_and_wal(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    with connect(db) as conn:
        names = {r["name"] for r in conn.execute("select name from sqlite_master where type='table'")}
        assert {"companies", "runs", "scores", "seen", "results", "ledger", "suppression"} <= names
        assert conn.execute("pragma journal_mode").fetchone()[0] == "wal"


def test_upsert_is_idempotent(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    with connect(db) as conn:
        assert upsert_companies(conn, [rec("CHE000000001"), rec("CHE000000002")], "r1") == 2
        assert upsert_companies(conn, [rec("CHE000000001")], "r2") == 1
        assert conn.execute("select count(*) from companies").fetchone()[0] == 2


def test_connection_rolls_back_on_error(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    try:
        with connect(db) as conn:
            upsert_companies(conn, [rec("CHE000000003")], "r1")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with connect(db) as conn:
        assert conn.execute("select count(*) from companies").fetchone()[0] == 0
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: company_reach.tools`

- [ ] **Step 3: Write schema.sql**

```sql
-- company-reach schema. Every row a run produces carries run_id.
CREATE TABLE IF NOT EXISTS companies (
  uid TEXT PRIMARY KEY, name TEXT NOT NULL, legal_form TEXT NOT NULL,
  municipality TEXT NOT NULL, street TEXT, postal_code TEXT, city TEXT,
  purpose TEXT NOT NULL, purpose_head TEXT NOT NULL,
  screen_reason TEXT, imported_at TEXT NOT NULL, import_run_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, goal TEXT, goal_hash TEXT, about_me TEXT, municipality TEXT,
  seed INTEGER, batch_size INTEGER, model TEXT, prompt_versions TEXT, criteria TEXT,
  started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, counts TEXT, cost_usd REAL);
CREATE TABLE IF NOT EXISTS scores (
  uid TEXT NOT NULL, goal_hash TEXT NOT NULL, prompt_version TEXT NOT NULL, model TEXT NOT NULL,
  score INTEGER NOT NULL, reason TEXT, scored_at TEXT NOT NULL,
  PRIMARY KEY (uid, goal_hash, prompt_version, model));
CREATE TABLE IF NOT EXISTS seen (
  uid TEXT PRIMARY KEY, run_id TEXT NOT NULL, batch_no INTEGER NOT NULL, drawn_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS searches (
  id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT, query TEXT, provider TEXT,
  results TEXT, unresponsive TEXT, chosen_url TEXT, tier TEXT, evidence TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS pages (
  url TEXT PRIMARY KEY, fetched_at TEXT, status INTEGER, text TEXT, raw_path TEXT);
CREATE TABLE IF NOT EXISTS profiles (
  run_id TEXT NOT NULL, uid TEXT NOT NULL, profile TEXT NOT NULL, PRIMARY KEY (run_id, uid));
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT, name TEXT, role TEXT, email TEXT,
  email_kind TEXT, source TEXT, source_url TEXT, linkedin_lead TEXT);
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT, contact_id INTEGER, subject TEXT, body TEXT,
  mailto_fits INTEGER, prompt_version TEXT, model TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS results (
  run_id TEXT NOT NULL, uid TEXT NOT NULL, recommendation TEXT, reason TEXT,
  error_kind TEXT, error_text TEXT, finished_at TEXT NOT NULL, PRIMARY KEY (run_id, uid));
CREATE TABLE IF NOT EXISTS ledger (
  uid TEXT PRIMARY KEY, status TEXT NOT NULL, address TEXT, draft_id INTEGER,
  decided_at TEXT NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS suppression (
  key TEXT PRIMARY KEY, reason TEXT, added_at TEXT NOT NULL);
```

- [ ] **Step 4: Write tools/db.py**

```python
"""SQLite access. One connection per call: LangGraph runs sync nodes in a
thread pool, and a sqlite3 connection must not cross threads. WAL lets the
review page read while a run writes; busy_timeout waits instead of failing
when two writers meet. `with conn:` commits on success and rolls back on an
exception — it does not close, so we close in the finally."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from company_reach.models import CompanyRecord


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, autocommit=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path: Path) -> None:
    schema = files("company_reach").joinpath("schema.sql").read_text()
    conn = _open(path)
    try:
        with conn:
            conn.executescript(schema)
    finally:
        conn.close()


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = _open(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def upsert_companies(conn: sqlite3.Connection, records: list[CompanyRecord], run_id: str) -> int:
    rows = [
        (r.uid, r.name, r.legal_form, r.municipality, r.street, r.postal_code, r.city,
         r.purpose, r.purpose_head, now(), run_id)
        for r in records
    ]
    conn.executemany(
        """INSERT INTO companies (uid, name, legal_form, municipality, street, postal_code,
             city, purpose, purpose_head, imported_at, import_run_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(uid) DO UPDATE SET name=excluded.name, purpose=excluded.purpose,
             purpose_head=excluded.purpose_head, street=excluded.street,
             postal_code=excluded.postal_code, city=excluded.city,
             imported_at=excluded.imported_at, import_run_id=excluded.import_run_id""",
        rows,
    )
    return len(rows)
```

`src/company_reach/tools/__init__.py` is empty. Add to `pyproject.toml` under `[tool.uv.build-backend]` nothing special — `uv_build` includes `schema.sql` because it sits inside the package; verify with `uv build` and `unzip -l dist/*.whl | grep schema.sql` if in doubt.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (3)

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/company_reach/schema.sql src/company_reach/tools tests/test_db.py
git commit -m "feat: sqlite schema and per-call connection helper"
```

---

### Task 4: LINDAS client

**Files:**
- Create: `src/company_reach/tools/lindas.py`
- Test: `tests/test_lindas.py`, `tests/fixtures/lindas_page.json`

**Interfaces:**
- Produces: `build_query(municipality: str, limit: int, offset: int) -> str`; `fetch_companies(municipality: str, *, url: str, page_size: int = 2000, client: httpx.Client | None = None) -> list[CompanyRecord]`; raises `LindasError` when the endpoint fails after retries or returns zero companies.

- [ ] **Step 1: Write the fixture**

`tests/fixtures/lindas_page.json` (fictional companies):

```json
{"head": {"vars": ["uid", "name", "lf", "desc", "street", "plz", "city"]},
 "results": {"bindings": [
  {"uid": {"type": "literal", "value": "CHE000000001"}, "name": {"type": "literal", "value": "Muster Metallbau AG"},
   "lf": {"type": "literal", "value": "0106"},
   "desc": {"type": "literal", "value": "Betrieb eines Metallbauunternehmens. Die Gesellschaft kann Zweigniederlassungen errichten."},
   "street": {"type": "literal", "value": "Musterstrasse 12"}, "plz": {"type": "literal", "value": "9000"},
   "city": {"type": "literal", "value": "St. Gallen"}},
  {"uid": {"type": "literal", "value": "CHE000000002"}, "name": {"type": "literal", "value": "Beispiel Immobilien GmbH"},
   "lf": {"type": "literal", "value": "0107"},
   "street": {"type": "literal", "value": "Beispielweg 4"}, "plz": {"type": "literal", "value": "9000"},
   "city": {"type": "literal", "value": "St. Gallen"}}
 ]}}
```

(The second company has no `desc`: that is the 3.2% case.)

- [ ] **Step 2: Write the failing test**

```python
import json
from pathlib import Path

import httpx
import pytest
import respx

from company_reach.tools.lindas import LindasError, build_query, fetch_companies

URL = "https://lindas.test/query"
PAGE = json.loads((Path(__file__).parent / "fixtures/lindas_page.json").read_text())
EMPTY = {"head": PAGE["head"], "results": {"bindings": []}}


def test_query_targets_municipality_and_legal_forms():
    q = build_query("3203", limit=10, offset=20)
    assert "<https://ld.admin.ch/municipality/3203>" in q
    assert "legalforms/0106" in q and "legalforms/0107" in q
    assert "LIMIT 10" in q and "OFFSET 20" in q


@respx.mock
def test_fetch_pages_until_empty_and_handles_missing_purpose():
    route = respx.post(URL).mock(side_effect=[httpx.Response(200, json=PAGE), httpx.Response(200, json=EMPTY)])
    records = fetch_companies("3203", url=URL, page_size=2)
    assert route.call_count == 2
    assert [r.uid for r in records] == ["CHE000000001", "CHE000000002"]
    assert records[0].purpose_head == "Betrieb eines Metallbauunternehmens."
    assert records[1].purpose == "" and records[1].city == "St. Gallen"


@respx.mock
def test_zero_companies_is_an_error():
    respx.post(URL).mock(return_value=httpx.Response(200, json=EMPTY))
    with pytest.raises(LindasError, match="3203"):
        fetch_companies("3203", url=URL)


@respx.mock
def test_server_error_retries_then_raises():
    route = respx.post(URL).mock(return_value=httpx.Response(503))
    with pytest.raises(LindasError):
        fetch_companies("3203", url=URL)
    assert route.call_count == 3
```

- [ ] **Step 3: Run it to see it fail**

Run: `uv run pytest tests/test_lindas.py -v`
Expected: FAIL — `ModuleNotFoundError: company_reach.tools.lindas`

- [ ] **Step 4: Write tools/lindas.py**

```python
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
    forms = ", ".join(f"<https://ld.admin.ch/ech/97/legalforms/{f}>" for f in _LEGAL_FORMS)
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
                url, data={"query": query},
                headers={"Accept": "application/sparql-results+json", "User-Agent": _UA},
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
        uid=v("uid") or "", name=v("name") or "", legal_form=v("lf") or "",
        municipality=municipality, street=v("street"), postal_code=v("plz"),
        city=v("city"), purpose=purpose, purpose_head=head_clause(purpose),
    )


def fetch_companies(
    municipality: str, *, url: str, page_size: int = 2000, client: httpx.Client | None = None
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
        raise LindasError(f"LINDAS returned no AG/GmbH companies for municipality {municipality}")
    return records
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_lindas.py -v`
Expected: PASS (4)

- [ ] **Step 6: Try it live, once**

Run: `uv run python -c "from company_reach.tools.lindas import fetch_companies; r=fetch_companies('3203', url='https://lindas.admin.ch/query'); print(len(r), r[0].name, r[0].postal_code)"`
Expected: about 5,460 records, a real name, `9000`-ish postal code. If the address fields come back `None` for every record, the predicate names differ from `data-sources.md` — check one company with `curl` and adjust the OPTIONAL block before continuing.

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/company_reach/tools/lindas.py tests/test_lindas.py tests/fixtures/lindas_page.json
git commit -m "feat: LINDAS client with paging and retries"
```

---

### Task 5: pool and screen as plain functions

**Files:**
- Create: `src/company_reach/nodes/__init__.py`, `src/company_reach/nodes/load_pool.py`, `src/company_reach/nodes/screen_pool.py`
- Test: `tests/test_pool_nodes.py`

**Interfaces:**
- Produces: `load_pool(municipality: str, run_id: str, *, settings: Settings) -> int` (companies stored); `screen_pool(run_id: str, *, settings: Settings) -> tuple[int, int]` (kept, dropped). In M3 both become graph nodes that read these arguments from state.

- [ ] **Step 1: Write the failing test**

```python
import json
from pathlib import Path

import httpx
import respx

from company_reach.nodes.load_pool import load_pool
from company_reach.nodes.screen_pool import screen_pool
from company_reach.tools.db import connect, init_db

PAGE = json.loads((Path(__file__).parent / "fixtures/lindas_page.json").read_text())


@respx.mock
def test_load_then_screen(settings):
    init_db(settings.db_path)
    respx.post(settings.lindas_url).mock(return_value=httpx.Response(200, json=PAGE))
    assert load_pool("3203", "r1", settings=settings) == 2
    kept, dropped = screen_pool("r1", settings=settings)
    assert (kept, dropped) == (1, 1)
    with connect(settings.db_path) as conn:
        rows = {r["uid"]: r["screen_reason"] for r in conn.execute("select uid, screen_reason from companies")}
    assert rows["CHE000000001"] is None
    assert rows["CHE000000002"] == "property only"
```

Note: the test `settings` fixture uses the default `lindas_url`; respx intercepts it, nothing leaves the machine.

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/test_pool_nodes.py -v`
Expected: FAIL — `ModuleNotFoundError: company_reach.nodes`

- [ ] **Step 3: Write the two functions**

`src/company_reach/nodes/load_pool.py`:

```python
"""Pool stage 1: LINDAS → companies table."""

from company_reach.settings import Settings
from company_reach.tools.db import connect, upsert_companies
from company_reach.tools.lindas import fetch_companies


def load_pool(municipality: str, run_id: str, *, settings: Settings) -> int:
    records = fetch_companies(municipality, url=settings.lindas_url)
    with connect(settings.db_path) as conn:
        return upsert_companies(conn, records, run_id)
```

`src/company_reach/nodes/screen_pool.py`:

```python
"""Pool stage 2: mark companies the rules exclude. Writes screen_reason;
NULL means kept."""

from company_reach.models import CompanyRecord
from company_reach.screen import screen_reason
from company_reach.settings import Settings
from company_reach.tools.db import connect


def screen_pool(run_id: str, *, settings: Settings) -> tuple[int, int]:
    kept = dropped = 0
    with connect(settings.db_path) as conn:
        rows = conn.execute(
            "select uid, name, legal_form, municipality, street, postal_code, city, purpose, purpose_head "
            "from companies where import_run_id = ?", (run_id,)
        ).fetchall()
        for row in rows:
            reason = screen_reason(CompanyRecord(**dict(row)))
            conn.execute("update companies set screen_reason = ? where uid = ?", (reason, row["uid"]))
            if reason is None:
                kept += 1
            else:
                dropped += 1
    return kept, dropped
```

`src/company_reach/nodes/__init__.py` is empty.

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_pool_nodes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/company_reach/nodes tests/test_pool_nodes.py
git commit -m "feat: load_pool and screen_pool functions"
```

---

### Task 6: CLI

**Files:**
- Create: `src/company_reach/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `app = typer.Typer()` with commands `pool --municipality 3203 [--run-id]` and `screen --run-id`.

- [ ] **Step 1: Write the failing test**

```python
import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from company_reach import cli

PAGE = json.loads((Path(__file__).parent / "fixtures/lindas_page.json").read_text())
runner = CliRunner()


@respx.mock
def test_pool_then_screen(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    respx.post(settings.lindas_url).mock(return_value=httpx.Response(200, json=PAGE))
    r = runner.invoke(cli.app, ["pool", "--municipality", "3203", "--run-id", "r1"])
    assert r.exit_code == 0, r.output
    assert "2 companies" in r.output
    r = runner.invoke(cli.app, ["screen", "--run-id", "r1"])
    assert r.exit_code == 0, r.output
    assert "kept 1" in r.output and "dropped 1" in r.output
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: company_reach.cli`

- [ ] **Step 3: Write cli.py**

```python
"""Command line. typer turns each function's signature into a command with
--help; the wiring to the `company-reach` executable is [project.scripts]."""

import uuid

import typer

from company_reach.nodes.load_pool import load_pool
from company_reach.nodes.screen_pool import screen_pool
from company_reach.settings import get_settings
from company_reach.tools.db import init_db

app = typer.Typer(help="Find Swiss companies, find the person, draft the mail.")


def _run_id(explicit: str | None) -> str:
    return explicit or f"r{uuid.uuid4().hex[:8]}"


@app.command()
def pool(municipality: str = "3203", run_id: str | None = None) -> None:
    """Fetch a municipality's AG+GmbH companies from LINDAS into the database."""
    s = get_settings()
    init_db(s.db_path)
    rid = _run_id(run_id)
    n = load_pool(municipality, rid, settings=s)
    typer.echo(f"{n} companies stored for municipality {municipality} (run {rid})")


@app.command()
def screen(run_id: str) -> None:
    """Apply the rule-based exclusions to the companies of a run."""
    s = get_settings()
    kept, dropped = screen_pool(run_id, settings=s)
    typer.echo(f"kept {kept}, dropped {dropped}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run the test, then the real thing**

Run: `uv run pytest -v` → all green.
Run: `uv run company-reach pool --municipality 3203 --run-id first` then `uv run company-reach screen --run-id first`.
Expected: about 5,460 stored; kept ≈ 4,000, dropped ≈ 1,450 (v0 measured 3,993 kept of 5,462).

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/company_reach/cli.py tests/test_cli.py
git commit -m "feat: pool and screen commands"
```

---

### Task 7: Explanation for Koray

**Files:**
- Create: `docs/milestones/m1-skeleton-and-pool.md`
- Modify: `README.md` (a "Run it" section: `uv sync`, copy `.env.example`, the two commands)

- [ ] **Step 1: Write the explanation** following the template in `IMPLEMENTATION.md` §5: what exists, libraries and why (uv, typer, pydantic-settings, httpx, sqlite3, respx), 3–5 things worth understanding with file:line anchors (e.g. `with conn:` semantics, `ON CONFLICT … DO UPDATE`, why `head_clause` runs before screening, why LINDAS pages by `?uid`, how respx fakes the network), where to look in reading order, "check this" with the two commands and one `sqlite3` query, open threads.

- [ ] **Step 2: Commit and open the PR**

```bash
git add docs/milestones/m1-skeleton-and-pool.md README.md
git commit -m "docs: M1 explanation and run instructions"
```

Then open the PR "feat: M1 skeleton and pool", referencing the M1 issue; the reviewer reads the code with the explanation open.

---

## Self-review

- **Spec coverage:** design §8 schema ✓ (Task 3), §10 settings ✓ (Task 1), §6 `lindas` contract (retry 3×, raise if empty) ✓ (Task 4), LEARNINGS §2 rules ✓ (Task 2), §11 layout ✓, explanation loop ✓ (Task 7). Not in M1 by design: LangGraph, LLM, search.
- **Placeholders:** none; every step carries code or a command.
- **Type consistency:** `CompanyRecord` fields match between models, lindas `_record`, db `upsert_companies` and screen_pool's `CompanyRecord(**dict(row))` (the `select` lists exactly the model's fields); `Settings.db_path` used by db/nodes/cli.
