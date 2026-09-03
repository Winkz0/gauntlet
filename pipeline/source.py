#!/usr/bin/env python3
"""
Sourcing driver. Run daily (cron / Task Scheduler) or on demand.

    python -m pipeline.source                # every company in the registry
    python -m pipeline.source --only Keeper  # substring match on company name
    python -m pipeline.source --refilter     # re-run the gate on stored jobs, no network

Pulls every company in adapters/registry.yaml via its board adapter,
enriches, applies the filter gate, dedupes against the DB, flags postings
that vanished from their board, and prints a summary. No LLM, no API spend.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters import boards                        # noqa: E402
from pipeline import enrich as E                   # noqa: E402
from pipeline import filters as F                  # noqa: E402
from pipeline import salary_osint as S             # noqa: E402
from pipeline import store                         # noqa: E402
from pipeline.config import db_path, load_cfg      # noqa: E402


def load_registry() -> dict:
    return yaml.safe_load((ROOT / "adapters" / "registry.yaml").read_text())


def process(con, cfg: dict, job: dict) -> tuple[int, bool, bool]:
    """Enrich, estimate salary, filter, store. Returns (job_id, passed, is_new)."""
    E.enrich(job)
    if job.get("salary_max") is None and cfg["filters"].get("allow_unverified_salary"):
        S.estimate_salary(job, metro=cfg["filters"].get("location_context", "Chicago"))
    ok, reasons = F.apply_filters(job, cfg)
    job_id, is_new = store.upsert_job(con, job)
    store.save_filter(con, job_id, ok, reasons)
    return job_id, ok, is_new


def run(only: str | None = None) -> None:
    cfg = load_cfg()
    reg = load_registry()
    db = db_path(cfg)
    store.init_db(db)
    con = store.connect(db)
    boards.TITLE_FILTER = lambda title: F.title_prefilter(title, cfg)

    totals = {"seen": 0, "new": 0, "passed": 0, "gone": 0}
    for entry in reg.get("companies", []):
        name = entry.get("name", "?")
        if only and only.lower() not in name.lower():
            continue
        problem = boards.validate_entry(entry)
        if problem:
            print(f"[skip] {name}: {problem}" + (f" ({entry['note']})" if entry.get("note") else ""))
            continue

        jobs = boards.pull(entry)
        if not jobs:
            print(f"[warn] {name}: 0 postings returned; not marking anything gone.")
            continue

        seen_ids: set[int] = set()
        n_new = n_pass = n_stub = 0
        for job in jobs:
            if job.get("prefiltered"):
                n_stub += 1
                row = store.find_job(con, job)
                if row:                      # stored earlier; keep it live, never re-store
                    store.touch(con, row["id"])
                    seen_ids.add(row["id"])
                continue
            job_id, ok, is_new = process(con, cfg, job)
            seen_ids.add(job_id)
            n_new += is_new
            n_pass += ok
        gone = store.mark_missing(con, name, entry["board"], seen_ids)
        con.commit()
        fetched = len(jobs) - n_stub
        print(f"[ok]   {name}: {len(jobs)} listings, {fetched} fetched, {n_new} new, "
              f"{n_pass} passed gate, {gone} gone")
        totals["seen"] += fetched; totals["new"] += n_new
        totals["passed"] += n_pass; totals["gone"] += gone

    con.close()
    print(f"\nSourcing complete: {totals['seen']} postings seen, {totals['new']} new, "
          f"{totals['passed']} passed the gate, {totals['gone']} marked gone.")
    print("Next: python -m pipeline.digest   (or run /morning-hunt in Claude Code)")


def refilter() -> None:
    """Re-run enrichment + gate on every stored job using the stored payload."""
    import json
    cfg = load_cfg()
    con = store.connect(db_path(cfg))
    store.migrate(con)
    rows = con.execute("SELECT * FROM jobs").fetchall()
    flipped = 0
    for r in rows:
        before = con.execute("SELECT passed FROM filter_results WHERE job_id=?", (r["id"],)).fetchone()
        job = {k: r[k] for k in r.keys() if k not in ("raw_json",)}
        job["raw"] = json.loads(r["raw_json"] or "{}")
        E.enrich(job)
        if job.get("salary_max") is None and cfg["filters"].get("allow_unverified_salary"):
            S.estimate_salary(job, metro=cfg["filters"].get("location_context", "Chicago"))
        ok, reasons = F.apply_filters(job, cfg)
        con.execute("""UPDATE jobs SET remote_type=?, employment_type=?, seniority=?,
                       salary_min=?, salary_max=?, salary_source=?, salary_note=? WHERE id=?""",
                    (job["remote_type"], job["employment_type"], job["seniority"],
                     job["salary_min"], job["salary_max"], job["salary_source"],
                     job.get("salary_note"), r["id"]))
        store.save_filter(con, r["id"], ok, reasons)
        if before and bool(before["passed"]) != ok:
            flipped += 1
            print(f"  #{r['id']} {r['company']} / {r['title']}: {'PASS' if ok else 'FAIL'}")
    con.commit()
    con.close()
    print(f"Refiltered {len(rows)} job(s); {flipped} changed verdict.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="only companies whose name contains this text")
    ap.add_argument("--refilter", action="store_true", help="re-run the gate on stored jobs, no network")
    args = ap.parse_args()
    if args.refilter:
        refilter()
    else:
        run(args.only)


if __name__ == "__main__":
    main()
