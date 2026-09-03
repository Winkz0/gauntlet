#!/usr/bin/env python3
"""
Record triage decisions and application stages from the terminal.

    python -m pipeline.decide <job_id> yes|no|skip [note...]
    python -m pipeline.decide <job_id> clear
    python -m pipeline.decide <job_id> stage <status> [--resume P] [--resume-pdf P]
                              [--cover P] [--defense P] [--gauntlet P]
    python -m pipeline.decide list [--all]

Decisions: yes keeps the role in the pipeline for tailoring, no and skip hide
it from future digests (skip means "not now", it archives after the retention
window like anything else).

Stages: drafted | queued_for_review | applied | interviewing | offer | rejected | closed.
Setting a stage on a job with no decision records a yes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import store                        # noqa: E402
from pipeline.config import db_path, load_cfg     # noqa: E402


def _job(con, job_id: int):
    row = con.execute("SELECT id, title, company FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        sys.exit(f"job {job_id} not found")
    return row


def cmd_list(con, show_all: bool) -> None:
    for r in store.board_rows(con):
        if not show_all and r["decision"] in ("no", "skip"):
            continue
        print(f"#{r['id']:>4} {r['decision'] or '-':4} {r['stage'] or '-':17} "
              f"{r['board_status']:4} {r['company'][:22]:22} {r['title'][:50]}")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    cfg = load_cfg()
    con = store.connect(db_path(cfg))
    store.migrate(con)

    if argv[:1] == ["list"]:
        cmd_list(con, "--all" in argv)
        con.close()
        return

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_id", type=int)
    ap.add_argument("action", help="yes | no | skip | clear | stage")
    ap.add_argument("rest", nargs="*", help="note text, or the stage name")
    ap.add_argument("--resume"); ap.add_argument("--resume-pdf"); ap.add_argument("--cover")
    ap.add_argument("--defense"); ap.add_argument("--gauntlet")
    a = ap.parse_args(argv)

    row = _job(con, a.job_id)
    if a.action in store.DECISIONS:
        store.set_decision(con, a.job_id, a.action, " ".join(a.rest))
        label = a.action
    elif a.action == "clear":
        store.clear_decision(con, a.job_id)
        label = "cleared"
    elif a.action == "stage":
        if not a.rest or a.rest[0] not in store.STAGES:
            sys.exit(f"stage must be one of: {', '.join(store.STAGES)}")
        paths = {k: v for k, v in {
            "resume_path": a.resume, "resume_pdf_path": a.resume_pdf, "cover_path": a.cover,
            "defense_path": a.defense, "gauntlet_json": a.gauntlet}.items() if v}
        store.set_stage(con, a.job_id, a.rest[0], **paths)
        cur = con.execute("SELECT decision FROM decisions WHERE job_id=?", (a.job_id,)).fetchone()
        if not cur:
            store.set_decision(con, a.job_id, "yes", "implied by stage")
        label = f"stage={a.rest[0]}"
    else:
        sys.exit("action must be yes | no | skip | clear | stage")
    con.commit()
    con.close()
    print(f"[{label}] {row['title']} at {row['company']}")


if __name__ == "__main__":
    main()
