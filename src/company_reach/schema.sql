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
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT, name TEXT, role TEXT, email TEXT,
  email_kind TEXT, source TEXT, source_url TEXT, source_date TEXT, linkedin_lead TEXT,
  alternatives TEXT, addresses TEXT, salutation TEXT);
-- model_text: the model's one sentence, from which the card rebuilds the
-- mail. frame_version and arm: which frame built the body (frame@1).
-- problems: check_draft's outcome; null = never checked, '' = passed. The
-- card sends only a draft that passed.
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT, contact_id INTEGER, subject TEXT, body TEXT,
  mailto_fits INTEGER, prompt_version TEXT, model TEXT, created_at TEXT,
  model_text TEXT, frame_version TEXT, arm TEXT, problems TEXT);
CREATE TABLE IF NOT EXISTS results (
  run_id TEXT NOT NULL, uid TEXT NOT NULL, recommendation TEXT, reason TEXT,
  error_kind TEXT, error_text TEXT, finished_at TEXT NOT NULL, PRIMARY KEY (run_id, uid));
-- One row per decision, never updated: a company's state is its latest row,
-- and "contacted" is any 'sent' row ever (M7 open point 4). A 'sent' row
-- keeps the frame, the A/B arm and the kind of contact ("generic/site/named"),
-- no personal data, so survey answers can be compared after forget and purge.
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY, uid TEXT NOT NULL, status TEXT NOT NULL, address TEXT,
  draft_id INTEGER, run_id TEXT, note TEXT, decided_at TEXT NOT NULL,
  frame_version TEXT, arm TEXT, contact_kind TEXT);
CREATE INDEX IF NOT EXISTS ledger_uid ON ledger (uid);
CREATE TABLE IF NOT EXISTS suppression (
  key TEXT PRIMARY KEY, reason TEXT, added_at TEXT NOT NULL);
-- Where find_site landed and why, for the review page, which reads only
-- SQLite: the site with its evidence, or no site and the searches tried.
CREATE TABLE IF NOT EXISTS sites (
  run_id TEXT NOT NULL, uid TEXT NOT NULL, url TEXT, tier TEXT, evidence TEXT,
  evidence_url TEXT, note TEXT, queries TEXT, candidates TEXT,
  PRIMARY KEY (run_id, uid));
