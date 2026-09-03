#!/usr/bin/env python3
"""
Digest builder + email sender.

    python -m pipeline.digest                   # daily: build + print (dry run)
    python -m pipeline.digest --send            # also email it and log the run
    python -m pipeline.digest --kind biweekly   # adds the "idle yes-roles" section

Selects gate-passing roles you have not triaged yet, ranks preferred
companies and strong-salary roles first, renders markdown, and optionally
emails it. Every sent digest is logged in the digests table. No LLM.
"""
from __future__ import annotations

import argparse
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import store                        # noqa: E402
from pipeline.config import db_path, load_cfg     # noqa: E402
from pipeline.filters import tags_from_reasons    # noqa: E402

BADGES = [
    ("preferred", "target company"),
    ("strong", "strong salary"),
    ("estimate", "estimated salary"),
    ("salary_unknown", "salary unlisted"),
    ("verify_role", "verify role fit"),
    ("verify_remote", "verify remote"),
    ("verify_employment", "verify hire type"),
    ("verify_location", "verify location"),
]


def _rank_key(row) -> tuple:
    tags = tags_from_reasons(row["reasons"])
    return (0 if "preferred" in tags else 1,
            0 if "strong" in tags else 1,
            1 if "verify_role" in tags else 0,
            row["company"].lower())


def build(con) -> list:
    return sorted(store.digest_candidates(con), key=_rank_key)


def _salary(r) -> str:
    if not r["salary_max"]:
        return "not listed"
    s = f"${r['salary_min']:,} to ${r['salary_max']:,}"
    if r["salary_source"] in ("geo_average", "osint_estimate"):
        s += " (estimate)"
    return s


def render_md(rows: list, stale: list | None = None, kind: str = "daily") -> str:
    lines = [f"# Gauntlet {kind} digest: {len(rows)} new role(s)\n"]
    if not rows:
        lines.append("No new roles cleared the gate.\n")
    for r in rows:
        tags = tags_from_reasons(r["reasons"])
        badges = [label for tag, label in BADGES if tag in tags]
        badge_str = f"  [{' | '.join(badges)}]" if badges else ""
        lines.append(
            f"## {r['title']} at {r['company']}{badge_str}\n"
            f"- Location / mode: {r['location'] or '?'} / {r['remote_type']}\n"
            f"- Salary: {_salary(r)}\n"
            f"- Hire type: {r['employment_type']}\n"
            f"- Link: {r['url']}\n"
            f"- Job id: `{r['id']}`\n"
        )
    if stale:
        lines.append(f"\n## Idle yes-roles ({len(stale)})\n")
        lines.append("Said yes, no application movement since:\n")
        for r in stale:
            lines.append(f"- `{r['id']}` {r['title']} at {r['company']}: "
                         f"{r['stage'] or 'no packet yet'}, last touched {(r['last_touched'] or '')[:10]}")
        lines.append("")
    lines.append(
        "\n---\nMark decisions with `python -m pipeline.decide <job_id> yes|no|skip`, "
        "or from the Sheet, then run `/morning-hunt` to tailor the yes-set.\n")
    return "\n".join(lines)


def send_email(cfg: dict, body_md: str, kind: str) -> None:
    to = cfg["notify"].get("email_to")
    if not to:
        sys.exit("EMAIL_TO is not set in config/secrets.env.")
    smtp = cfg["smtp"]
    msg = MIMEText(body_md, "plain", "utf-8")
    msg["Subject"] = f"Gauntlet {kind} digest"
    msg["From"] = smtp["sender"]
    msg["To"] = to
    with smtplib.SMTP(smtp["host"], smtp["port"]) as s:
        if smtp["user"]:
            s.starttls()
            s.login(smtp["user"], smtp["password"])
        s.send_message(msg)
    print(f"Emailed digest to {to}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="email the digest and log the run")
    ap.add_argument("--kind", choices=("daily", "biweekly"), default="daily")
    args = ap.parse_args()

    cfg = load_cfg()
    con = store.connect(db_path(cfg))
    store.migrate(con)
    rows = build(con)
    stale = store.stale_yes(con, int(cfg["notify"].get("stale_after_days", 14))) \
        if args.kind == "biweekly" else None
    md = render_md(rows, stale, args.kind)
    print(md)
    if args.send:
        send_email(cfg, md, args.kind)
        store.record_digest(con, args.kind, [r["id"] for r in rows])
        con.commit()
    con.close()


if __name__ == "__main__":
    main()
