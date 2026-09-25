-- company-reach schema. Every row a run produces carries run_id.
CREATE TABLE IF NOT EXISTS companies (
  uid TEXT PRIMARY KEY, name TEXT NOT NULL, legal_form TEXT NOT NULL,
  municipality TEXT NOT NULL, street TEXT, postal_code TEXT, city TEXT,
  purpose TEXT NOT NULL, purpose_head TEXT NOT NULL,
  screen_reason TEXT, imported_at TEXT NOT NULL, import_run_id TEXT NOT NULL);
-- One row per `score` command: its criteria, then its end — status 'done'
-- with counts, 'failed', or still 'scoring' if the command was killed. The
-- criteria of goals scored before the criteria table are read from here.
-- `run` writes no row: its record is data/runs/<id>/manifest.json. about_me,
-- municipality, batch_size and cost_usd are never written; they stay so old
-- databases and new ones keep one shape.
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, goal TEXT, goal_hash TEXT, about_me TEXT, municipality TEXT,
  seed INTEGER, batch_size INTEGER, model TEXT, prompt_versions TEXT, criteria TEXT,
  started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, counts TEXT, cost_usd REAL);
-- criteria_hash names the criteria a score was made against; NULL for a
-- score made before criteria were stored (audit H10).
CREATE TABLE IF NOT EXISTS scores (
  uid TEXT NOT NULL, goal_hash TEXT NOT NULL, prompt_version TEXT NOT NULL, model TEXT NOT NULL,
  score INTEGER NOT NULL, reason TEXT, scored_at TEXT NOT NULL, criteria_hash TEXT,
  PRIMARY KEY (uid, goal_hash, prompt_version, model));
-- One set of criteria per goal, so every `score` pass ranks against the
-- rules the user reviewed. `--new-criteria` replaces the row, and the set
-- it replaces moves to criteria_history.
CREATE TABLE IF NOT EXISTS criteria (
  goal_hash TEXT PRIMARY KEY, criteria TEXT NOT NULL, criteria_hash TEXT NOT NULL,
  model TEXT, prompt_version TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS criteria_history (
  id INTEGER PRIMARY KEY, goal_hash TEXT NOT NULL, criteria TEXT NOT NULL,
  criteria_hash TEXT NOT NULL, model TEXT, prompt_version TEXT,
  created_at TEXT NOT NULL, replaced_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS seen (
  uid TEXT PRIMARY KEY, run_id TEXT NOT NULL, batch_no INTEGER NOT NULL, drawn_at TEXT NOT NULL);
-- Where each company of a run stands while the run works on it: the step of
-- its graph it entered last (issue #60). The front page reads it; `results`
-- says how the company ended. A step's name only: no page text, no person.
CREATE TABLE IF NOT EXISTS progress (
  run_id TEXT NOT NULL, uid TEXT NOT NULL, stage TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (run_id, uid));
-- The search log (#20): one row per provider asked for one of find_site's
-- queries, errors included. `results` holds SearXNG's first ten URLs and is
-- NULL for Brave, whose terms forbid storing its results.
CREATE TABLE IF NOT EXISTS searches (
  id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT, query TEXT, provider TEXT,
  results TEXT, unresponsive TEXT, chosen_url TEXT, tier TEXT, evidence TEXT, at TEXT,
  result_count INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS pages (
  url TEXT PRIMARY KEY, fetched_at TEXT, status INTEGER, text TEXT, raw_path TEXT);
CREATE TABLE IF NOT EXISTS profiles (
  run_id TEXT NOT NULL, uid TEXT NOT NULL, profile TEXT NOT NULL, PRIMARY KEY (run_id, uid));
-- salutation: "Frau"/"Herr" as the page wrote it or the reviewer chose it,
-- "ohne" when the reviewer chose none; null when nobody stated one.
-- salutation_origin: "reviewer", "page" (before the full name) or
-- "page-surname" (before the surname alone).
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT, name TEXT, role TEXT, email TEXT,
  email_kind TEXT, source TEXT, source_url TEXT, source_date TEXT, linkedin_lead TEXT,
  alternatives TEXT, addresses TEXT, salutation TEXT, salutation_origin TEXT);
-- model_text: the model's one sentence, from which the card rebuilds the
-- mail. frame_version and arm: which frame built the body (frame@1).
-- problems: check_draft's outcome; null = never checked, '' = passed. The
-- card sends only a draft that passed. AUTOINCREMENT: an id is never used
-- twice, so a sent row's draft_id names the text that went out or nothing.
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, uid TEXT, contact_id INTEGER,
  subject TEXT, body TEXT,
  mailto_fits INTEGER, prompt_version TEXT, model TEXT, created_at TEXT,
  model_text TEXT, frame_version TEXT, arm TEXT, problems TEXT);
-- needs_js: how many of the pages read came back as a JavaScript shell
-- (read_pages); null when the child did not say — it failed, or the row was
-- written by code that reads no pages.
CREATE TABLE IF NOT EXISTS results (
  run_id TEXT NOT NULL, uid TEXT NOT NULL, recommendation TEXT, reason TEXT,
  error_kind TEXT, error_text TEXT, finished_at TEXT NOT NULL, needs_js INTEGER,
  PRIMARY KEY (run_id, uid));
-- One row per decision, never updated: a company's state is its latest row,
-- and "contacted" is any 'sent' row ever (M7 open point 4) that no later
-- 'not_sent' or 'bounced' row took back; `reverses` names the 'sent' row
-- such a row takes back, by id, since forget clears addresses. A 'sent' row
-- keeps the frame, the A/B arm and the kind of contact ("generic/site/named"),
-- no personal data, so survey answers can be compared after forget and purge.
-- It also snapshots what went out — the subject, the body's sha256 and the
-- draft's prompt version — so a later redraft, retry or purge cannot change
-- the record. The subject can name the person: forget and purge clear it
-- (and forget the address), the rest stays.
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY, uid TEXT NOT NULL, status TEXT NOT NULL, address TEXT,
  draft_id INTEGER, run_id TEXT, note TEXT, decided_at TEXT NOT NULL,
  frame_version TEXT, arm TEXT, contact_kind TEXT,
  subject TEXT, body_sha256 TEXT, prompt_version TEXT, reverses INTEGER);
CREATE INDEX IF NOT EXISTS ledger_uid ON ledger (uid);
-- The survey's export, joined to the ledger by the UID in the link. Replaced
-- whole on every import; a UID without a 'sent' row is kept and counted.
CREATE TABLE IF NOT EXISTS responses (
  uid TEXT PRIMARY KEY, started_at TEXT, completed_at TEXT, imported_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS suppression (
  key TEXT PRIMARY KEY, reason TEXT, added_at TEXT NOT NULL);
-- Where find_site landed and why, for the review page, which reads only
-- SQLite: the site with its evidence, or no site and the searches tried.
CREATE TABLE IF NOT EXISTS sites (
  run_id TEXT NOT NULL, uid TEXT NOT NULL, url TEXT, tier TEXT, evidence TEXT,
  evidence_url TEXT, note TEXT, queries TEXT, candidates TEXT,
  PRIMARY KEY (run_id, uid));
