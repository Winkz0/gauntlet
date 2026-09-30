"""
The filter gate. Pure deterministic rules, no LLM. Returns (passed, reasons).

Every reason is a dict: {"rule", "ok", "detail", optional "tag", "source"}.
Tags surface in the digest and the Sheet so unverified fields are visible.

Dealbreakers (hard fail):
  - title matches a role_keywords_block term
  - no role keyword or role family in the title, and no (gate term in title
    + keyword in body)
  - remote_type is onsite
  - hybrid role whose location does not mention the configured metro
  - location matches a location_block term
  - employment_type in employment_types_block
  - salary known and max < floor
  - unnamed company (see is_named) with no salary at all, when
    filters.unnamed_require_salary is on
  - seniority below seniority_min, or below the lowest min_level of the
    role families the title belongs to
  - optional filters.level_cap: for the listed companies, seniority outside
    [floor, ceiling], or an explicit ladder number (L5, E5, "Engineer 5")
    above max_level_number

Soft signals (tag only): unknown salary, unknown employment, unknown remote,
role matched only in the body, location not clearly in an ok region,
preferred-company boost, strong salary, no level in a level-capped title or
a tag_unlabeled family, unnamed company (general_search).
"""
from __future__ import annotations

import re

from pipeline.enrich import has_level_signal, parse_level_number

SENIORITY_ORDER = ["analyst_i", "analyst_ii", "senior", "lead", "principal"]


def _seniority_ok(job_sen: str | None, floor: str) -> bool:
    try:
        return SENIORITY_ORDER.index(job_sen or "") >= SENIORITY_ORDER.index(floor)
    except ValueError:
        return True  # unknown -> don't block


def _level_cap(job: dict, f: dict) -> dict | None:
    """The filters.level_cap block if it applies to this job's company."""
    cap = f.get("level_cap") or {}
    companies = {c.lower() for c in cap.get("companies") or []}
    return cap if (job.get("company") or "").lower() in companies else None


def _level_cap_check(job: dict, cap: dict) -> tuple[bool, dict]:
    """An explicit ladder number decides when present; otherwise the seniority label does."""
    title = job.get("title") or ""
    level = parse_level_number(title)
    max_level = cap.get("max_level_number")
    if level is not None and max_level is not None:
        if level > int(max_level):
            return False, {"rule": "level_cap", "ok": False, "detail": f"level {level} > {max_level}"}
        return True, {"rule": "level_cap", "ok": True, "detail": f"level {level}"}
    ceiling = cap.get("ceiling")
    sen = job.get("seniority")
    if ceiling and sen in SENIORITY_ORDER and ceiling in SENIORITY_ORDER \
            and SENIORITY_ORDER.index(sen) > SENIORITY_ORDER.index(ceiling):
        return False, {"rule": "level_cap", "ok": False, "detail": f"{sen} > {ceiling}"}
    if cap.get("tag_unlabeled") and not has_level_signal(title):
        return True, {"rule": "level_cap", "ok": True, "detail": "no level in title",
                      "tag": "verify_level"}
    return True, {"rule": "level_cap", "ok": True, "detail": sen}


def _has_term(text: str, terms: list[str]) -> list[str]:
    """Whole-word, case-insensitive match; returns the terms that hit."""
    hits = []
    for t in terms or []:
        if re.search(r"(?<![a-z0-9])" + re.escape(t.lower()) + r"(?![a-z0-9])", text):
            hits.append(t)
    return hits


def _word_rx(word: str) -> str:
    """One variant word as a whole-word regex; a trailing * matches any ending."""
    if word.endswith("*"):
        return r"(?<![a-z0-9])" + re.escape(word[:-1])
    return r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])"


def role_families(title: str, cfg: dict) -> list[str]:
    """Names of the role families with a variant whose words all appear in the title."""
    t = (title or "").lower()
    hits = []
    for name, fam in (cfg.get("role_families") or {}).items():
        for variant in (fam or {}).get("variants") or []:
            words = str(variant).lower().split()
            if words and all(re.search(_word_rx(w), t) for w in words):
                hits.append(name)
                break
    return hits


def _family_floor(families: list[str], cfg: dict) -> tuple[str, str | None]:
    """
    (seniority floor, family that set it). A family without a valid min_level
    uses seniority_min; across several families the lowest floor wins. No
    family: seniority_min.
    """
    base = cfg["filters"]["seniority_min"]
    options = []
    for name in families:
        lvl = (cfg["role_families"][name] or {}).get("min_level")
        options.append((lvl, name) if lvl in SENIORITY_ORDER else (base, None))
    if not options:
        return base, None
    return min(options, key=lambda o: SENIORITY_ORDER.index(o[0]) if o[0] in SENIORITY_ORDER
               else len(SENIORITY_ORDER))


def is_named(job: dict, cfg: dict) -> bool:
    """
    A company the human named: in targets_preferred or the registry (the
    sourcing driver puts registry names in cfg["named_companies"]). A manual
    intake counts as named, since the human picked it.
    """
    if str(job.get("source") or "").startswith("manual"):
        return True
    names = {str(c).lower() for c in (cfg.get("targets_preferred") or []) + (cfg.get("named_companies") or [])}
    return (job.get("company") or "").lower() in names


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
                or role_families(t, cfg)
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
    families = role_families(title_l, cfg)
    if title_kw or families:
        reasons.append({"rule": "role_keyword", "ok": True, "detail": (title_kw + families)[:3]})
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

    # --- company: named, or found by the general search ---
    named = is_named(job, cfg)
    if not named:
        reasons.append({"rule": "company", "ok": True, "detail": "not a named company",
                        "tag": "general_search"})

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
    elif f.get("allow_unverified_salary", True) and (named or not f.get("unnamed_require_salary")):
        reasons.append({"rule": "salary", "ok": True, "detail": "not listed",
                        "source": "none", "tag": "salary_unknown"})
    else:
        passed = False
        reasons.append({"rule": "salary", "ok": False,
                        "detail": "not listed" + ("" if named else " (unnamed company)")})

    # --- seniority floor (a level cap, else the role families, can move it) ---
    cap = _level_cap(job, f)
    fam_floor, fam_source = _family_floor(families, cfg)
    sen_floor = (cap or {}).get("floor") or fam_floor
    sen_reason = {"rule": "seniority", "ok": True, "detail": job.get("seniority")}
    if not _seniority_ok(job.get("seniority"), sen_floor):
        passed = False
        sen_reason = {"rule": "seniority", "ok": False,
                      "detail": f"{job.get('seniority')} < {sen_floor}"
                                + (f" ({fam_source} floor)" if fam_source and not cap else "")}
    elif any((cfg["role_families"][n] or {}).get("tag_unlabeled") for n in families) \
            and not has_level_signal(job.get("title") or ""):
        sen_reason.update(detail="no level in title", tag="verify_level")
    reasons.append(sen_reason)

    # --- level cap ceiling ---
    if cap:
        ok, reason = _level_cap_check(job, cap)
        passed = passed and ok
        reasons.append(reason)

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
