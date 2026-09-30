#!/usr/bin/env python3
"""
Sourcing driver. Run daily (cron / Task Scheduler) or on demand.

    python -m pipeline.source                # registry companies, then the general search
    python -m pipeline.source --only Keeper  # substring match on company name, no general search
    python -m pipeline.source --no-search    # registry companies only
    python -m pipeline.source --search-only  # general search only
    python -m pipeline.source --refilter     # re-run the gate on stored jobs, no network

Pulls every company in adapters/registry.yaml via its board adapter, then
runs the role family queries through the general search sources in
adapters/search.py. Enriches, applies the filter gate, dedupes against the
DB, flags postings that vanished, and prints a summary. No LLM, no Claude
session budget.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters import boards                        # noqa: E402
from adapters import search as SR                  # noqa: E402
from pipeline import enrich as E                   # noqa: E402
from pipeline import filters as F                  # noqa: E402
from pipeline import salary_osint as S             # noqa: E402
from pipeline import store                         # noqa: E402
from pipeline.config import db_path, load_cfg, merge_local  # noqa: E402

REGISTRY_PATH = ROOT / "adapters" / "registry.yaml"
REGISTRY_LOCAL_PATH = ROOT / "adapters" / "registry.local.yaml"


def load_registry(path: Path = REGISTRY_PATH, local_path: Path | None = REGISTRY_LOCAL_PATH) -> dict:
    """The committed registry, layered with adapters/registry.local.yaml (gitignored) if present."""
    reg = yaml.safe_load(path.read_text()) or {}
    if local_path is not None and local_path.exists():
        merge_local(reg, yaml.safe_load(local_path.read_text()) or {})
    return reg


def named_companies(cfg: dict, reg: dict) -> list[str]:
    """Every company the human named: registry entries (wired or not) plus targets_preferred."""
    names = [e.get("name") for e in reg.get("companies", []) if e.get("name")]
    return sorted(set(names) | set(cfg.get("targets_preferred") or []))


# Words that vary between an employer's listings without changing who it is.
_CORP_FILLER = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp", "corporation",
    "co", "company", "holdings", "holding", "group", "plc", "the", "com", "web",
    "services", "platforms", "labs", "technologies", "technology", "americas", "usa", "us",
}


def _company_key(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in _CORP_FILLER and len(w) > 1)


def canonical_company(name: str, named: list[str]) -> str:
    """
    The named company a search result's employer name refers to, else the
    name unchanged. "Amazon Web Services, Inc." -> "Amazon", "Huntress Labs"
    -> "Huntress". Matching is exact after dropping corporate filler, so
    "Apple Leisure Group" stays itself.
    """
    key = _company_key(name)
    for n in named:
        if key and key == _company_key(n):
            return n
    return name


def process(con, cfg: dict, job: dict) -> tuple[int, bool, bool]:
    """Enrich, estimate salary, filter, store. Returns (job_id, passed, is_new)."""
    E.enrich(job)
    if job.get("salary_max") is None and cfg["filters"].get("allow_unverified_salary"):
        S.estimate_salary(job, metro=cfg["filters"].get("location_context", "Chicago"))
    ok, reasons = F.apply_filters(job, cfg)
    job_id, is_new = store.upsert_job(con, job)
    store.save_filter(con, job_id, ok, reasons)
    return job_id, ok, is_new


def run_search(con, cfg: dict, reg: dict, totals: dict) -> None:
    """
    Every general search source. Results at a registry company with a
    working adapter are dropped (the adapter pulls the full posting), titles
    that could never pass are not stored, and a posting another source
    already stored is only kept live, never overwritten with a snippet.
    """
    s = cfg.get("search") or {}
    if not s.get("enabled", True):
        print("[skip] general search: disabled in config")
        return
    wired = {e["name"] for e in reg.get("companies", []) if e.get("name") and not boards.validate_entry(e)}
    named = cfg["named_companies"]
    for name, fetch in SR.SOURCES.items():
        try:
            jobs = fetch(cfg)
        except SR.Skip as e:
            print(f"[skip] search {name}: {e}")
            continue
        n_cov = n_off = n_other = n_new = n_pass = 0
        for job in jobs:
            job["company"] = canonical_company(job["company"], named)
            if job["company"] in wired:
                n_cov += 1
                continue
            if not F.title_prefilter(job["title"], cfg):
                n_off += 1
                continue
            row = store.find_job(con, job)
            if row and row["source"] != job["source"]:
                store.touch(con, row["id"])
                n_other += 1
                continue
            _, ok, is_new = process(con, cfg, job)
            n_new += is_new
            n_pass += ok
        expired = store.expire_unseen(con, name, int(s.get("expire_after_days", 14))) if jobs else 0
        con.commit()
        kept = len(jobs) - n_cov - n_off
        print(f"[ok]   search {name}: {len(jobs)} results, {n_cov} at registry companies, "
              f"{n_off} off-target titles, {n_other} already stored from another source, "
              f"{n_new} new, {n_pass} passed gate, {expired} expired")
        totals["seen"] += kept; totals["new"] += n_new
        totals["passed"] += n_pass; totals["gone"] += expired


def run(only: str | None = None, registry: bool = True, search: bool = True) -> None:
    cfg = load_cfg()
    reg = load_registry()
    cfg["named_companies"] = named_companies(cfg, reg)
    db = db_path(cfg)
    store.init_db(db)
    con = store.connect(db)
    boards.TITLE_FILTER = lambda title: F.title_prefilter(title, cfg)

    totals = {"seen": 0, "new": 0, "passed": 0, "gone": 0}
    for entry in reg.get("companies", []) if registry else []:
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

    if search and not only:
        run_search(con, cfg, reg, totals)
    con.close()
    print(f"\nSourcing complete: {totals['seen']} postings seen, {totals['new']} new, "
          f"{totals['passed']} passed the gate, {totals['gone']} marked gone.")
    print("Next: python -m pipeline.digest   (or run /morning-hunt in Claude Code)")


def refilter() -> None:
    """Re-run enrichment + gate on every stored job using the stored payload."""
    import json
    cfg = load_cfg()
    cfg["named_companies"] = named_companies(cfg, load_registry())
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
    ap.add_argument("--only", help="only registry companies whose name contains this text (skips the general search)")
    scope = ap.add_mutually_exclusive_group()
    scope.add_argument("--no-search", action="store_true", help="registry companies only")
    scope.add_argument("--search-only", action="store_true", help="general search only")
    ap.add_argument("--refilter", action="store_true", help="re-run the gate on stored jobs, no network")
    args = ap.parse_args()
    if args.refilter:
        refilter()
    else:
        run(args.only, registry=not args.search_only, search=not args.no_search)


if __name__ == "__main__":
    main()
