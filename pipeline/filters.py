"""
The filter gate. Pure deterministic rules, no LLM. Returns (passed, reasons).

Every reason is a dict: {"rule", "ok", "detail", optional "tag", "source"}.
Tags surface in the digest and the Sheet so unverified fields are visible.

Dealbreakers (hard fail):
  - title matches a role_keywords_block term
  - no role keyword in the title, and no (gate term in title + keyword in body)
  - remote_type is onsite
  - hybrid role whose location does not mention the configured metro
  - location matches a location_block term
  - employment_type in employment_types_block
  - salary known and max < floor
  - seniority below seniority_min

Soft signals (tag only): unknown salary, unknown employment, unknown remote,
role matched only in the body, location not clearly in an ok region,
preferred-company boost, strong salary.
"""
from __future__ import annotations

import re

SENIORITY_ORDER = ["analyst_i", "analyst_ii", "senior", "lead", "principal"]


def _seniority_ok(job_sen: str | None, floor: str) -> bool:
    try:
        return SENIORITY_ORDER.index(job_sen or "") >= SENIORITY_ORDER.index(floor)
    except ValueError:
        return True  # unknown -> don't block


def _has_term(text: str, terms: list[str]) -> list[str]:
    """Whole-word, case-insensitive match; returns the terms that hit."""
    hits = []
    for t in terms or []:
        if re.search(r"(?<![a-z0-9])" + re.escape(t.lower()) + r"(?![a-z0-9])", text):
            hits.append(t)
    return hits


def title_prefilter(title: str, cfg: dict) -> bool:
    """
    Cheap title-only pre-check used before a board adapter spends a request
    on the full posting. True means "worth fetching". Mirrors the title
    rules in apply_filters exactly, so nothing that could pass is skipped.
    """
    t = (title or "").lower()
    if _has_term(t, cfg.get("role_keywords_block", [])):
        return False
    return bool(_has_term(t, cfg.get("role_keywords_any", []))
                or _has_term(t, cfg["filters"].get("title_gate_terms", [])))


def apply_filters(job: dict, cfg: dict) -> tuple[bool, list[dict]]:
    f = cfg["filters"]
    reasons: list[dict] = []
    passed = True

    title_l = (job.get("title") or "").lower()
    desc_l = (job.get("description") or "").lower()
    loc_l = (job.get("location") or "").lower()

    # --- blocklist (title only) ---
    blocked = _has_term(title_l, cfg.get("role_keywords_block", []))
    if blocked:
        passed = False
        reasons.append({"rule": "blocklist", "ok": False, "detail": blocked})

    # --- role keyword match ---
    title_kw = _has_term(title_l, cfg.get("role_keywords_any", []))
    if title_kw:
        reasons.append({"rule": "role_keyword", "ok": True, "detail": title_kw[:3]})
    else:
        gate = _has_term(title_l, f.get("title_gate_terms", []))
        body_kw = _has_term(desc_l, cfg.get("role_keywords_any", [])) if gate else []
        if body_kw:
            reasons.append({"rule": "role_keyword", "ok": True, "tag": "verify_role",
                            "detail": f"body only: {', '.join(body_kw[:3])}"})
        else:
            passed = False
            reasons.append({"rule": "role_keyword", "ok": False,
                            "detail": "no target role keyword in title"
                                      + ("" if gate else " and title is not security-flavored")})

    # --- remote type ---
    rt = job.get("remote_type", "unknown")
    metro = (f.get("location_context") or "").split(",")[0].strip().lower()
    if rt == "unknown":
        rest = re.sub(r"\b(remote|hybrid|on[- ]?site|usa|us|united states( of america)?|\d+ locations)\b",
                      " ", loc_l)
        named_place = bool(re.search(r"[a-z]", rest))
        if f.get("unknown_mode_requires_metro", True) and named_place and metro and metro not in loc_l:
            passed = False
            reasons.append({"rule": "remote", "ok": False,
                            "detail": f"mode not stated and location is outside {metro.title()}: {job.get('location')}"})
        else:
            reasons.append({"rule": "remote", "ok": True, "detail": "unknown (verify)",
                            "tag": "verify_remote"})
    elif rt not in f["remote_types_ok"]:
        passed = False
        reasons.append({"rule": "remote", "ok": False, "detail": rt})
    elif rt == "hybrid" and f.get("hybrid_requires_metro", True):
        if loc_l and metro and metro not in loc_l and not re.search(r"\d+ locations", loc_l):
            passed = False
            reasons.append({"rule": "remote", "ok": False,
                            "detail": f"hybrid outside {metro.title()}: {job.get('location')}"})
        else:
            reasons.append({"rule": "remote", "ok": True, "detail": rt,
                            **({"tag": "verify_location"} if not loc_l or metro not in loc_l else {})})
    else:
        reasons.append({"rule": "remote", "ok": True, "detail": rt})

    # --- geography ---
    # Strip mode words; what is left is the place. "USA - Remote" -> "usa",
    # "Remote" -> "" (no place stated), "3 Locations" -> multi.
    place = re.sub(r"\b(remote|hybrid|on[- ]?site|us[- ]remote)\b", " ", loc_l)
    place = re.sub(r"[^a-z0-9 ]", " ", place).strip()
    multi = bool(re.search(r"\d+ locations", loc_l))
    geo_block = _has_term(loc_l, f.get("location_block", []))
    geo_ok = _has_term(loc_l, f.get("location_ok", []))
    if geo_block:
        passed = False
        reasons.append({"rule": "geo", "ok": False, "detail": geo_block})
    elif geo_ok:
        reasons.append({"rule": "geo", "ok": True, "detail": geo_ok[:2]})
    elif not place or multi:
        reasons.append({"rule": "geo", "ok": True, "tag": "verify_location",
                        "detail": f"location not stated: {job.get('location') or '?'}"})
    elif f.get("geo_strict", True):
        passed = False
        reasons.append({"rule": "geo", "ok": False,
                        "detail": f"location outside ok regions: {job.get('location')}"})
    else:
        reasons.append({"rule": "geo", "ok": True, "tag": "verify_location",
                        "detail": f"location not clearly in an ok region: {job.get('location') or '?'}"})

    # --- employment type ---
    et = job.get("employment_type", "unknown")
    if et in f["employment_types_block"]:
        passed = False
        reasons.append({"rule": "employment", "ok": False, "detail": et})
    elif et == "unknown":
        reasons.append({"rule": "employment", "ok": True, "detail": "unknown (verify)",
                        "tag": "verify_employment"})
    else:
        reasons.append({"rule": "employment", "ok": True, "detail": et})

    # --- salary ---
    smax, smin = job.get("salary_max"), job.get("salary_min")
    ssrc = job.get("salary_source", "none")
    floor = f["salary_floor"]
    if smax is not None:
        if smax < floor:
            passed = False
            reasons.append({"rule": "salary", "ok": False,
                            "detail": f"{smin}-{smax} < floor {floor}", "source": ssrc})
        else:
            tag = "strong" if smax >= f["salary_target"] else "ok"
            if ssrc in ("osint_estimate", "geo_average"):
                tag = "estimate"
            reasons.append({"rule": "salary", "ok": True,
                            "detail": f"{smin}-{smax}", "source": ssrc, "tag": tag})
    elif f.get("allow_unverified_salary", True):
        reasons.append({"rule": "salary", "ok": True, "detail": "not listed",
                        "source": "none", "tag": "salary_unknown"})
    else:
        passed = False
        reasons.append({"rule": "salary", "ok": False, "detail": "not listed"})

    # --- seniority floor ---
    if _seniority_ok(job.get("seniority"), f["seniority_min"]):
        reasons.append({"rule": "seniority", "ok": True, "detail": job.get("seniority")})
    else:
        passed = False
        reasons.append({"rule": "seniority", "ok": False,
                        "detail": f"{job.get('seniority')} < {f['seniority_min']}"})

    # --- preferred company boost (soft) ---
    if job.get("company") in cfg.get("targets_preferred", []):
        reasons.append({"rule": "preferred_company", "ok": True,
                        "detail": job["company"], "tag": "preferred"})

    return passed, reasons


def tags_from_reasons(reasons) -> set[str]:
    """Tolerant tag extraction: accepts the list of dicts, legacy strings, or JSON."""
    import json
    if isinstance(reasons, str):
        try:
            reasons = json.loads(reasons or "[]")
        except json.JSONDecodeError:
            return set()
    tags = set()
    for r in reasons or []:
        if isinstance(r, dict) and r.get("tag"):
            tags.add(r["tag"])
    return tags
