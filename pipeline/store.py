"""
Storage + dedup layer over SQLite.

Dedup: a posting is the same job if (source, source_job_id) matches, or,
failing that, if fingerprint = sha256(company | normalized_title |
normalized_location) matches. Re-seen jobs get their mutable fields
refreshed (salary can appear later, descriptions get edited).

State model
-----------
decisions.decision      yes | no | skip           human triage, one per job
applications.status     drafted | queued_for_review | applied | interviewing
                        | offer | rejected | closed
jobs.board_status       live | gone               set by the sourcing run

Legacy databases used decision values like "applied" and "rejected"; migrate()
moves those into applications.status and rewrites the decision to "yes".
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).resolve().parent.parent / "data" / "schema.sql"

DECISIONS = ("yes", "no", "skip")
STAGES = ("drafted", "queued_for_review", "applied", "interviewing", "offer", "rejected", "closed")
ACTIVE_STAGES = ("drafted", "queued_for_review", "applied", "interviewing", "offer")
LEGACY_DECISION_TO_STAGE = {
    "applied": "applied", "interviewing": "interviewing",
    "rejected": "rejected", "closed": "closed",
}

# Mutable job fields refreshed on every re-sighting.
_REFRESH_FIELDS = ("title", "location", "remote_type", "url", "description",
                   "salary_min", "salary_max", "salary_source", "salary_note",
                   "employment_type", "seniority")


def _norm(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"\b(senior|sr|jr|junior|i{1,3}|\d)\b", "", s)  # collapse level noise
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fingerprint(company: str, title: str, location: str) -> str:
    key = f"{_norm(company)}|{_norm(title)}|{_norm(location)}"
    return hashlib.sha256(key.encode()).hexdigest()


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def migrate(con: sqlite3.Connection) -> list[str]:
    """Bring an older database up to the current schema. Idempotent."""
    done: list[str] = []
    if "board_status" not in _columns(con, "jobs"):
        con.execute("ALTER TABLE jobs ADD COLUMN board_status TEXT NOT NULL DEFAULT 'live'")
        con.execute("ALTER TABLE jobs ADD COLUMN gone_since TEXT")
        done.append("jobs.board_status")
    if "updated_at" not in _columns(con, "applications"):
        con.execute("ALTER TABLE applications ADD COLUMN updated_at TEXT")
        con.execute("UPDATE applications SET updated_at = created_at WHERE updated_at IS NULL")
        done.append("applications.updated_at")
    con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_source_id ON jobs(source, source_job_id)")

    # Legacy lifecycle values stored as decisions -> applications.status
    for r in con.execute("SELECT job_id, decision FROM decisions").fetchall():
        stage = LEGACY_DECISION_TO_STAGE.get(r["decision"])
        if stage:
            set_stage(con, r["job_id"], stage)
            con.execute("UPDATE decisions SET decision='yes' WHERE job_id=?", (r["job_id"],))
            done.append(f"decision {r['job_id']}: {r['decision']} -> stage")

    # Legacy filter reasons stored as plain strings -> dict form
    for r in con.execute("SELECT job_id, reasons FROM filter_results").fetchall():
        try:
            reasons = json.loads(r["reasons"] or "[]")
        except json.JSONDecodeError:
            continue
        if any(not isinstance(x, dict) for x in reasons):
            fixed = [x if isinstance(x, dict) else {"rule": "legacy", "ok": True, "detail": str(x)}
                     for x in reasons]
            con.execute("UPDATE filter_results SET reasons=? WHERE job_id=?",
                        (json.dumps(fixed), r["job_id"]))
            done.append(f"filter_results {r['job_id']}: normalized reasons")
    con.commit()
    return done


def init_db(db_path: str) -> None:
    con = connect(db_path)
    con.executescript(SCHEMA.read_text())
    con.commit()
    migrate(con)
    con.close()


# ------------------------------------------------------------------- jobs
def find_job(con: sqlite3.Connection, job: dict) -> sqlite3.Row | None:
    if job.get("source") and job.get("source_job_id"):
        row = con.execute("SELECT * FROM jobs WHERE source=? AND source_job_id=?",
                          (job["source"], str(job["source_job_id"]))).fetchone()
        if row:
            return row
    fp = fingerprint(job["company"], job["title"], job.get("location", ""))
    return con.execute("SELECT * FROM jobs WHERE fingerprint = ?", (fp,)).fetchone()


def upsert_job(con: sqlite3.Connection, job: dict) -> tuple[int, bool]:
    """Insert or refresh. Returns (job_id, is_new)."""
    row = find_job(con, job)
    if row:
        changes = {k: job.get(k) for k in _REFRESH_FIELDS
                   if k in job and job.get(k) not in (None, "") and job.get(k) != row[k]}
        sets = ", ".join(f"{k}=?" for k in changes)
        sql = "UPDATE jobs SET last_seen=datetime('now'), board_status='live', gone_since=NULL"
        if sets:
            sql += ", " + sets
        con.execute(sql + " WHERE id=?", (*changes.values(), row["id"]))
        return row["id"], False

    fp = fingerprint(job["company"], job["title"], job.get("location", ""))
    cur = con.execute(
        """INSERT INTO jobs
           (fingerprint, source, source_job_id, company, title, location,
            remote_type, url, description, salary_min, salary_max,
            salary_source, salary_note, employment_type, seniority, raw_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fp, job.get("source"), str(job.get("source_job_id") or "") or None, job["company"],
         job["title"], job.get("location"), job.get("remote_type"), job["url"],
         job.get("description"), job.get("salary_min"), job.get("salary_max"),
         job.get("salary_source"), job.get("salary_note"),
         job.get("employment_type"), job.get("seniority"),
         json.dumps(job.get("raw", {}), default=str)),
    )
    return cur.lastrowid, True


def touch(con: sqlite3.Connection, job_id: int) -> None:
    """Re-sighting without new data: keep the job live and bump last_seen."""
    con.execute("UPDATE jobs SET last_seen=datetime('now'), board_status='live', gone_since=NULL WHERE id=?",
                (job_id,))


def mark_missing(con: sqlite3.Connection, company: str, source: str, seen_ids: set[int]) -> int:
    """
    After a full pull of one company, flag its live jobs that were not in the
    pull as gone from the board. Only called when the pull itself succeeded.
    """
    rows = con.execute(
        "SELECT id FROM jobs WHERE company=? AND source=? AND board_status='live'",
        (company, source)).fetchall()
    missing = [r["id"] for r in rows if r["id"] not in seen_ids]
    if missing:
        con.executemany(
            "UPDATE jobs SET board_status='gone', gone_since=datetime('now') WHERE id=?",
            [(i,) for i in missing])
    return len(missing)


def save_filter(con: sqlite3.Connection, job_id: int, passed: bool, reasons: list) -> None:
    con.execute(
        """INSERT INTO filter_results (job_id, passed, reasons)
           VALUES (?,?,?)
           ON CONFLICT(job_id) DO UPDATE SET
             passed=excluded.passed, reasons=excluded.reasons,
             checked_at=datetime('now')""",
        (job_id, 1 if passed else 0, json.dumps(reasons)),
    )


# -------------------------------------------------------------- decisions
def set_decision(con: sqlite3.Connection, job_id: int, decision: str, note: str = "") -> None:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
    con.execute(
        """INSERT INTO decisions (job_id, decision, note) VALUES (?,?,?)
           ON CONFLICT(job_id) DO UPDATE SET
             decision=excluded.decision, note=excluded.note,
             decided_at=datetime('now')""",
        (job_id, decision, note),
    )


def clear_decision(con: sqlite3.Connection, job_id: int) -> None:
    con.execute("DELETE FROM decisions WHERE job_id=?", (job_id,))


# ----------------------------------------------------------- applications
def set_stage(con: sqlite3.Connection, job_id: int, status: str, **paths) -> None:
    """Create or update the application row for a job."""
    if status not in STAGES:
        raise ValueError(f"status must be one of {STAGES}, got {status!r}")
    cols = {k: v for k, v in paths.items()
            if k in ("resume_path", "resume_pdf_path", "cover_path", "defense_path", "gauntlet_json")}
    row = con.execute("SELECT id FROM applications WHERE job_id=?", (job_id,)).fetchone()
    if row:
        sets = ", ".join(f"{k}=?" for k in cols)
        sql = "UPDATE applications SET status=?, updated_at=datetime('now')"
        if sets:
            sql += ", " + sets
        if status == "applied":
            sql += ", submitted_at=COALESCE(submitted_at, datetime('now'))"
        con.execute(sql + " WHERE job_id=?", (status, *cols.values(), job_id))
    else:
        names = ["job_id", "status", *cols]
        con.execute(
            f"INSERT INTO applications ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
            (job_id, status, *cols.values()))


# ----------------------------------------------------------------- views
BOARD_SQL = """
    SELECT j.*, fr.reasons, fr.passed,
           COALESCE(d.decision, '')   AS decision,
           d.decided_at,
           d.note                     AS decision_note,
           COALESCE(a.status, '')     AS stage,
           a.updated_at               AS stage_updated_at,
           a.resume_path,
           MAX(COALESCE(d.decided_at, ''), COALESCE(a.updated_at, ''), j.first_seen) AS last_touched
    FROM jobs j
    JOIN filter_results fr ON fr.job_id = j.id
    LEFT JOIN decisions d    ON d.job_id = j.id
    LEFT JOIN applications a ON a.job_id = j.id
"""


def board_rows(con: sqlite3.Connection, passed_only: bool = True) -> list[sqlite3.Row]:
    """Every job on the working board with its triage and lifecycle state."""
    where = "WHERE fr.passed = 1" if passed_only else ""
    return con.execute(BOARD_SQL + f" {where} ORDER BY j.first_seen DESC").fetchall()


def digest_candidates(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Passed the gate, still on the board, no triage decision yet."""
    return con.execute(
        BOARD_SQL + """
        WHERE fr.passed = 1 AND d.job_id IS NULL AND j.board_status = 'live'
        ORDER BY j.last_seen DESC"""
    ).fetchall()


def stale_yes(con: sqlite3.Connection, days: int) -> list[sqlite3.Row]:
    """Yes-decisions with no lifecycle movement in `days`."""
    return con.execute(
        BOARD_SQL + """
        WHERE d.decision = 'yes'
          AND COALESCE(a.status, 'drafted') IN ('drafted', 'queued_for_review')
          AND julianday('now') - julianday(
                MAX(COALESCE(d.decided_at, ''), COALESCE(a.updated_at, ''), j.first_seen)) >= ?
        ORDER BY last_touched ASC""", (days,)
    ).fetchall()


def record_digest(con: sqlite3.Connection, kind: str, job_ids: list[int]) -> None:
    con.execute("INSERT INTO digests (kind, job_ids) VALUES (?, ?)", (kind, json.dumps(job_ids)))
