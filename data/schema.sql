-- Gauntlet SQLite source of truth.
-- The Google Sheet is a synced view; this database is authoritative.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Every job we've ever seen, from any source. Dedup on (source, source_job_id)
-- first, then on fingerprint.
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint     TEXT NOT NULL UNIQUE,   -- sha256(company|normalized_title|location)
    source          TEXT NOT NULL,          -- greenhouse | lever | ashby | workday | eightfold | amazon | manual_* | ...
    source_job_id   TEXT,                   -- native id on the board, if any
    company         TEXT NOT NULL,
    title           TEXT NOT NULL,
    location        TEXT,
    remote_type     TEXT,                   -- remote | hybrid | onsite | unknown
    url             TEXT NOT NULL,
    description     TEXT,                   -- full posting text
    salary_min      INTEGER,
    salary_max      INTEGER,
    salary_source   TEXT,                   -- posting | osint_estimate | geo_average | none
    salary_note     TEXT,
    employment_type TEXT,                   -- direct | contract | staffing | unknown
    seniority       TEXT,                   -- analyst_i | analyst_ii | senior | lead | principal
    first_seen      TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen       TEXT NOT NULL DEFAULT (datetime('now')),
    board_status    TEXT NOT NULL DEFAULT 'live',   -- live | gone (no longer on the board)
    gone_since      TEXT,
    raw_json        TEXT                    -- original adapter payload, for re-parsing
);

-- Result of running a job through the deterministic filter gate.
CREATE TABLE IF NOT EXISTS filter_results (
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    passed          INTEGER NOT NULL,       -- 1 / 0
    reasons         TEXT,                   -- JSON array of {"rule","ok","detail","tag"?}
    checked_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (job_id)
);

-- Triage ledger: the human's yes / no / skip per job. This is what keeps a
-- role out of future digests. Lifecycle after a yes lives in applications.
CREATE TABLE IF NOT EXISTS decisions (
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    decision        TEXT NOT NULL,          -- yes | no | skip
    decided_at      TEXT NOT NULL DEFAULT (datetime('now')),
    note            TEXT,
    PRIMARY KEY (job_id)
);

-- One row per job the human said yes to. Tracks the tailored artifacts and
-- the application lifecycle.
CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    resume_path     TEXT,
    resume_pdf_path TEXT,
    cover_path      TEXT,
    defense_path    TEXT,
    gauntlet_json   TEXT,
    status          TEXT NOT NULL DEFAULT 'drafted',
        -- drafted | queued_for_review | applied | interviewing | offer | rejected | closed
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    submitted_at    TEXT
);

-- Digest run log, so the biweekly view knows what was already sent.
CREATE TABLE IF NOT EXISTS digests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,          -- daily | biweekly
    job_ids         TEXT,                   -- JSON array
    sent_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_company   ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen ON jobs(last_seen);
CREATE INDEX IF NOT EXISTS idx_jobs_source_id ON jobs(source, source_job_id);
CREATE INDEX IF NOT EXISTS idx_decisions_dec  ON decisions(decision);
