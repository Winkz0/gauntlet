#!/usr/bin/env python3
"""
Google Sheet mirror: a phone-friendly view of the SQLite board with two
human-editable columns.

    python -m pipeline.sheet_sync            # pull edits, then rewrite both tabs
    python -m pipeline.sheet_sync --pull     # Sheet -> DB only
    python -m pipeline.sheet_sync --push     # DB -> Sheet only (still pulls first, see below)
    python -m pipeline.sheet_sync --init     # create/format the worksheets
    python -m pipeline.sheet_sync --dry-run  # print what would move where, touch nothing

Design
------
SQLite is authoritative. The Sheet has two tabs:

  pipeline   the working board: live postings you have not archived
  archive    rows that retired: untouched for `archive_after_days`, gone from
             the board for `gone_grace_days`, or in a terminal stage

Two columns are yours to edit, both dropdowns:

  decision   yes | no | skip        triage. Clearing the cell undoes the decision.
  stage      drafted | queued_for_review | applied | interviewing | offer
             | rejected | closed     lifecycle after a yes.

Every push rewrites both tabs from the database, so the Sheet can never
drift. To avoid clobbering a phone edit that has not been read yet, push
always pulls first. Rows are matched on the job id, never on position, so
sorting or filtering on mobile is safe.

Auth: Google service account, see config/secrets/README.md.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import store                        # noqa: E402
from pipeline.config import db_path, load_cfg     # noqa: E402
from pipeline.filters import tags_from_reasons    # noqa: E402

HEADERS = [
    "id", "decision", "stage", "company", "title", "location", "remote_type",
    "salary", "hire_type", "seniority", "tags", "days_open", "last_touched",
    "board_status", "url", "first_seen",
]
EDITABLE = {"decision": list(store.DECISIONS), "stage": list(store.STAGES)}
TERMINAL_STAGES = {"rejected", "closed"}


# ------------------------------------------------------------------ client
def _client(cfg: dict):
    """Lazily import gspread so the rest of the pipeline never depends on it."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        sys.exit("Missing deps. Run: pip install gspread google-auth")

    sa_path = ROOT / cfg["sheet"]["service_account_file"]
    if not sa_path.exists():
        sys.exit(f"Service account JSON not found at {sa_path}. See config/secrets/README.md.")
    scopes = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(str(sa_path), scopes=scopes)
    return gspread.authorize(creds).open_by_key(cfg["sheet"]["sheet_id"])


def _worksheet(sh, name: str):
    import gspread
    try:
        return sh.worksheet(name)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=name, rows=200, cols=len(HEADERS))


def _format(ws) -> None:
    ws.clear()
    ws.update([HEADERS], "A1")
    ws.format("A1:P1", {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                        "backgroundColorStyle": {"rgbColor": {"red": 0.15, "green": 0.15, "blue": 0.18}}})
    ws.freeze(rows=1)
    try:
        from gspread.utils import ValidationConditionType
        for col, values in EDITABLE.items():
            letter = chr(ord("A") + HEADERS.index(col))
            ws.add_validation(f"{letter}2:{letter}2000", ValidationConditionType.one_of_list,
                              values, showCustomUi=True)
    except Exception as e:  # noqa: BLE001, validation is a nicety
        print(f"[note] could not set dropdowns ({e}); columns still editable.")


# -------------------------------------------------------------------- rows
def _days_since(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        t = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - t).days


def _salary(r) -> str:
    if not r["salary_max"]:
        return ""
    s = f"${r['salary_min']:,}-${r['salary_max']:,}"
    if r["salary_source"] in ("geo_average", "osint_estimate"):
        s += " (est)"
    return s


def classify(row, cfg: dict) -> str:
    """Return 'pipeline' or 'archive' for a board row."""
    sc = cfg["sheet"]
    if row["stage"] in TERMINAL_STAGES:
        return "archive"
    if row["board_status"] == "gone":
        if row["stage"] in store.ACTIVE_STAGES and row["stage"] not in ("drafted", "queued_for_review"):
            return "pipeline"          # you already applied; keep tracking it
        if (_days_since(row["gone_since"]) or 0) >= int(sc.get("gone_grace_days", 3)):
            return "archive"
    if row["decision"] == "no":
        return "archive"
    if row["stage"] in ("applied", "interviewing", "offer"):
        return "pipeline"
    idle = _days_since(row["last_touched"]) or 0
    if idle >= int(sc.get("archive_after_days", 8)):
        return "archive"
    return "pipeline"


def build_rows(con, cfg: dict) -> dict[str, list[list[str]]]:
    out = {"pipeline": [], "archive": []}
    for r in store.board_rows(con):
        rec = {
            "id": r["id"],
            "decision": r["decision"],
            "stage": r["stage"],
            "company": r["company"],
            "title": r["title"],
            "location": r["location"] or "",
            "remote_type": r["remote_type"] or "",
            "salary": _salary(r),
            "hire_type": r["employment_type"] or "",
            "seniority": r["seniority"] or "",
            "tags": ", ".join(sorted(tags_from_reasons(r["reasons"]))),
            "days_open": _days_since(r["first_seen"]),
            "last_touched": (r["last_touched"] or "")[:10],
            "board_status": r["board_status"],
            "url": r["url"],
            "first_seen": (r["first_seen"] or "")[:10],
        }
        out[classify(r, cfg)].append([("" if rec[h] is None else str(rec[h])) for h in HEADERS])
    return out


# -------------------------------------------------------------------- pull
def _read_edits(ws) -> dict[int, dict[str, str]]:
    rows = ws.get_all_values()
    if not rows or rows[0] != HEADERS:
        return {}
    idx = {h: HEADERS.index(h) for h in ("id", "decision", "stage")}
    edits = {}
    for row in rows[1:]:
        if len(row) <= idx["stage"] or not row[idx["id"]].strip().isdigit():
            continue
        edits[int(row[idx["id"]])] = {
            "decision": row[idx["decision"]].strip().lower(),
            "stage": row[idx["stage"]].strip().lower(),
        }
    return edits


def pull(cfg: dict, con, sh=None) -> int:
    """Apply decision and stage edits from both tabs to the database."""
    sh = sh or _client(cfg)
    edits: dict[int, dict[str, str]] = {}
    for name in (cfg["sheet"]["worksheet"], cfg["sheet"].get("archive_worksheet", "archive")):
        try:
            edits.update(_read_edits(_worksheet(sh, name)))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] could not read tab {name}: {e}")

    current = {r["id"]: r for r in store.board_rows(con)}
    changed = 0
    for jid, e in edits.items():
        row = current.get(jid)
        if row is None:
            continue
        dec, stage = e["decision"], e["stage"]
        if dec != row["decision"]:
            if dec == "":
                store.clear_decision(con, jid)
                changed += 1
            elif dec in store.DECISIONS:
                store.set_decision(con, jid, dec, note="via sheet")
                changed += 1
        if stage != row["stage"] and stage in store.STAGES:
            store.set_stage(con, jid, stage)
            if not row["decision"] and dec == "":
                store.set_decision(con, jid, "yes", note="stage set via sheet")
            changed += 1
    con.commit()
    print(f"Pull: applied {changed} edit(s) from Sheet -> DB.")
    return changed


# -------------------------------------------------------------------- push
def push(cfg: dict, con, sh=None, skip_pull: bool = False) -> dict[str, int]:
    """Rewrite both tabs from the database. Pulls first unless told not to."""
    sh = sh or _client(cfg)
    if not skip_pull:
        pull(cfg, con, sh)
    tabs = build_rows(con, cfg)
    counts = {}
    for key, name in (("pipeline", cfg["sheet"]["worksheet"]),
                      ("archive", cfg["sheet"].get("archive_worksheet", "archive"))):
        ws = _worksheet(sh, name)
        existing = ws.get_all_values()
        if not existing or existing[0] != HEADERS:
            _format(ws)
        else:
            ws.batch_clear([f"A2:P{max(len(existing), 2)}"])
        if tabs[key]:
            ws.update(tabs[key], "A2", value_input_option="RAW")
        counts[key] = len(tabs[key])
    print(f"Push: {counts['pipeline']} row(s) on '{cfg['sheet']['worksheet']}', "
          f"{counts['archive']} on '{cfg['sheet'].get('archive_worksheet', 'archive')}'.")
    return counts


def dry_run(cfg: dict, con) -> None:
    tabs = build_rows(con, cfg)
    for key in ("pipeline", "archive"):
        print(f"--- {key} ({len(tabs[key])})")
        for row in tabs[key]:
            rec = dict(zip(HEADERS, row))
            print(f"  #{rec['id']:>4} {rec['decision'] or '-':4} {rec['stage'] or '-':17} "
                  f"{rec['company'][:22]:22} {rec['title'][:40]:40} idle={rec['last_touched']} {rec['board_status']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", action="store_true", help="create/format both worksheets")
    ap.add_argument("--push", action="store_true", help="DB -> Sheet (pulls first)")
    ap.add_argument("--pull", action="store_true", help="Sheet -> DB only")
    ap.add_argument("--dry-run", action="store_true", help="show tab assignment, no network")
    args = ap.parse_args()

    cfg = load_cfg()
    con = store.connect(db_path(cfg))
    store.migrate(con)

    if args.dry_run:
        dry_run(cfg, con)
        con.close()
        return
    if not cfg.get("sheet", {}).get("enabled", False):
        sys.exit("sheet.enabled is false in config.yaml.")
    if not cfg["sheet"].get("sheet_id"):
        sys.exit("Set SHEET_ID in config/secrets.env first.")

    sh = _client(cfg)
    if args.init:
        for name in (cfg["sheet"]["worksheet"], cfg["sheet"].get("archive_worksheet", "archive")):
            _format(_worksheet(sh, name))
        print("Initialized worksheets.")
    elif args.pull:
        pull(cfg, con, sh)
    else:
        push(cfg, con, sh)
    con.close()


if __name__ == "__main__":
    main()
