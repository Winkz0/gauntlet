"""
Enrichment: turn raw adapter dicts into filterable structured fields.

Parses (heuristically, no LLM):
  - salary_min / salary_max  (from structured comp fields or posting text)
  - remote_type              (remote | hybrid | onsite | unknown)
  - employment_type          (direct | contract | staffing | unknown)
  - seniority                (analyst_i | analyst_ii | senior | lead | principal)

Rules of thumb that keep the false-positive rate down:
  - The location string wins over the description for remote/hybrid. Many
    postings say "100% remote, with a hybrid option near HQ" in the body.
  - A salary needs a currency marker: a "$", a "k" suffix, or comma-grouped
    thousands. "100-150 employees" is not a salary.
  - "contract" only counts in an employment phrase. "negotiate contract
    renewals" in a sales posting is not a contract role.

When salary is absent, salary_source is left as "none" here; the geo
estimate is applied separately in salary_osint.py so it's easy to disable.
"""
from __future__ import annotations

import re

# ---- salary ---------------------------------------------------------------
# A money token must carry some marker that it is money.
_MONEY = (
    r"(?:\$\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?"   # $120,000
    r"|\$\s*\d{4,6}(?:\.\d+)?"                # $120000
    r"|\$\s*\d{2,3}(?:\.\d)?\s*[kK]\b"        # $120k
    r"|\b\d{2,3}(?:\.\d)?\s*[kK]\b"           # 120k
    r"|\b\d{1,3}(?:,\d{3})+\b)"               # 120,000
)
RANGE_RX = re.compile(_MONEY + r"\s*(?:-|–|—|to|and|through)\s*" + _MONEY, re.IGNORECASE)
MONEY_RX = re.compile(_MONEY, re.IGNORECASE)
SALARY_MIN, SALARY_MAX = 30_000, 700_000


_SALARY_CONTEXT = re.compile(
    r"(salary|base pay|pay range|compensation|comp range|per year|annually|annual|/\s*yr|a year)",
    re.IGNORECASE)


def _to_int(token: str) -> int | None:
    t = token.lower().replace("$", "").replace(",", "").strip()
    if re.fullmatch(r"401\s*k", t):          # retirement plan, not pay
        return None
    mult = 1
    if t.endswith("k"):
        mult = 1000
        t = t[:-1].strip()
    try:
        n = int(float(t) * mult)
    except ValueError:
        return None
    return n if SALARY_MIN <= n <= SALARY_MAX else None


def _structured_salary(raw: dict | None) -> tuple[int | None, int | None] | None:
    """Ashby ships a compensation block; other boards do not."""
    if not isinstance(raw, dict):
        return None
    comp = raw.get("compensation")
    if not isinstance(comp, dict):
        return None
    vals = []
    for t in comp.get("compensationTiers") or []:
        for c in t.get("components", []):
            for k in ("minValue", "maxValue", "value"):
                v = c.get(k)
                if isinstance(v, (int, float)) and SALARY_MIN <= v <= SALARY_MAX:
                    vals.append(int(v))
    return (min(vals), max(vals)) if vals else None


def parse_salary(text: str, raw: dict | None = None) -> tuple[int | None, int | None, str]:
    s = _structured_salary(raw)
    if s:
        return s[0], s[1], "posting"
    if not text:
        return None, None, "none"

    for m in RANGE_RX.finditer(text):
        a, b = MONEY_RX.findall(m.group(0))[:2]
        lo, hi = _to_int(a), _to_int(b)
        if lo and hi and lo <= hi:
            return lo, hi, "posting"

    # Single amounts only count when they sit near a salary word; otherwise
    # "$500,000 bonus pool" or "$40,000 signing" become the range.
    amounts = []
    for m in MONEY_RX.finditer(text):
        n = _to_int(m.group(0))
        if n and _SALARY_CONTEXT.search(text[max(0, m.start() - 80): m.end() + 80]):
            amounts.append(n)
    if amounts:
        return min(amounts), max(amounts), "posting"
    return None, None, "none"


# ---- remote ---------------------------------------------------------------
_REMOTE_STRONG = r"\b(100\s*%\s*remote|fully remote|remote[- ]first|work from home|wfh|remote (position|role|opportunity|job)|this (position|role) is remote)\b"
_REMOTE_WEAK = r"\bremote\b"
_HYBRID = r"\bhybrid\b"
_ONSITE = r"\b(on[- ]?site|in[- ]?office|in[- ]person)\b"


def parse_remote(text: str, location: str) -> str:
    loc = (location or "").lower()
    if re.search(_REMOTE_WEAK, loc):
        return "remote"
    if re.search(_HYBRID, loc):
        return "hybrid"
    if re.search(_ONSITE, loc):
        return "onsite"

    blob = (text or "").lower()
    if re.search(_REMOTE_STRONG, blob):
        return "remote"
    if re.search(_HYBRID, blob):
        return "hybrid"
    if re.search(_REMOTE_WEAK, blob):
        return "remote"
    if re.search(_ONSITE, blob):
        return "onsite"
    return "unknown"


# ---- employment -----------------------------------------------------------
_CONTRACT = re.compile(
    r"\b(contract (position|role|opportunity|basis|assignment|to hire|employee)"
    r"|contract-to-hire|contractor (role|position)|(6|12|18)[- ]month contract"
    r"|c2c|corp[- ]to[- ]corp|1099|w-?2 contract"
    r"|temporary (position|role|assignment)|temp[- ]to[- ]perm)\b", re.IGNORECASE)
_STAFFING = re.compile(
    r"\b(staffing (agency|firm|partner)|recruit(ing|ment) (agency|firm)|placement agency"
    r"|on behalf of (our|a) client|our client is (seeking|looking|hiring))\b", re.IGNORECASE)
_DIRECT = re.compile(r"\b(full[- ]time|permanent|direct hire|fte|regular employee)\b", re.IGNORECASE)


def _structured_employment(raw: dict | None) -> str | None:
    """Boards that state the schedule type structurally."""
    if not isinstance(raw, dict):
        return None
    detail = raw.get("detail") if isinstance(raw.get("detail"), dict) else raw
    for key in ("timeType", "job_schedule_type", "employmentType", "type"):
        v = detail.get(key)
        if isinstance(v, str):
            vl = v.lower()
            if "full" in vl or "regular" in vl:
                return "direct"
            if "contract" in vl or "temp" in vl:
                return "contract"
    return None


def parse_employment(text: str, raw: dict | None = None) -> str:
    blob = text or ""
    if _CONTRACT.search(blob):
        return "contract"
    if _STAFFING.search(blob):
        return "staffing"
    structured = _structured_employment(raw)
    if structured:
        return structured
    if _DIRECT.search(blob):
        return "direct"
    return "unknown"


# ---- seniority ------------------------------------------------------------
SENIORITY_MAP = [
    (r"\b(principal|staff|distinguished|director|vice president|vp)\b", "principal"),
    (r"\b(lead|manager|head of)\b", "lead"),
    (r"\b(senior|sr\.?|iii|3)\b", "senior"),
    (r"\b(ii|2)\b", "analyst_ii"),
    (r"\b(associate|assoc\.?|i|1|junior|jr\.?|entry|intern)\b", "analyst_i"),
]


def parse_seniority(title: str) -> str:
    t = (title or "").lower()
    for rx, label in SENIORITY_MAP:
        if re.search(rx, t):
            return label
    return "analyst_ii"  # assume mid unless signalled otherwise


def enrich(job: dict) -> dict:
    text = job.get("description", "") or ""
    raw = job.get("raw")
    lo, hi, src = parse_salary(text, raw)
    job["salary_min"] = lo
    job["salary_max"] = hi
    job["salary_source"] = src
    job["remote_type"] = parse_remote(text, job.get("location", ""))
    job["employment_type"] = parse_employment(text, raw)
    job["seniority"] = parse_seniority(job.get("title", ""))
    return job
