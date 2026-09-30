"""
General search: keyword search across every employer through official job
search APIs. No scraping, no logins.

    adzuna    GET api.adzuna.com/v1/api/jobs/{country}/search/1   free key, developer.adzuna.com
    usajobs   GET data.usajobs.gov/api/search                    free key, developer.usajobs.gov

Each source takes the loaded config and returns the same normalized dicts as
adapters/boards.py. The queries are the role_families `queries` in
config.yaml. Adzuna returns a description snippet, not the full posting, so
remote mode and hire type are confirmed less often; enrich.py reads the
salary and contract fields both APIs return as data.

A source that is disabled or has no key raises Skip. A failed query is
reported and the rest still run. Error messages never include the request
URL, because Adzuna takes its key as a query parameter.
"""
from __future__ import annotations

import re
from typing import Callable

import requests

from adapters.boards import _get, _strip_html

ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"
USAJOBS_URL = "https://data.usajobs.gov/api/search"


class Skip(Exception):
    """The source is disabled or has no API key."""


def _safe(e: Exception) -> str:
    """An error message with any URL query string (and so any key) removed."""
    return re.sub(r"\?\S*", "?...", str(e))


def _failed(source: str, q: str, e: requests.RequestException) -> None:
    """Report a failed query; a rejected key stops the source instead of spending quota on every query."""
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status in (401, 403):
        raise Skip(f"key rejected (HTTP {status}); check config/secrets.env")
    print(f"[warn] {source} '{q}' failed: {_safe(e)}")


def search_queries(cfg: dict) -> list[str]:
    """Every role family query, in config order, without repeats."""
    seen: set[str] = set()
    out = []
    for fam in (cfg.get("role_families") or {}).values():
        for q in (fam or {}).get("queries") or []:
            q = str(q).strip()
            if q and q.lower() not in seen:
                seen.add(q.lower())
                out.append(q)
    return out


def _metro(cfg: dict) -> str:
    return (cfg["filters"].get("location_context") or "").split(",")[0].strip()


# -------------------------------------------------------------------- adzuna
def _adzuna_job(j: dict, country: str) -> dict:
    display = ((j.get("location") or {}).get("display_name") or "").strip()
    cc = country.upper()
    # Every result is in `country`. Saying so keeps a remote role listed under
    # an out-of-metro city from reading as a foreign location to the geo gate.
    location = display if re.search(rf"\b{re.escape(cc)}\b", display) else ", ".join(filter(None, [display, cc]))
    return {
        "source": "adzuna",
        "source_job_id": str(j.get("id") or ""),
        "company": ((j.get("company") or {}).get("display_name") or "").strip() or "Unknown",
        "title": _strip_html(j.get("title")),
        "location": location,
        "url": j.get("redirect_url", ""),
        "description": _strip_html(j.get("description")),
        "raw": j,
    }


def adzuna(cfg: dict) -> list[dict]:
    """
    One call per query per pass, newest first, title match only. Passes:
    postings that mention remote, and postings within distance_km of
    location_context (skipped when no metro is set).
    """
    s = cfg.get("search") or {}
    a = s.get("adzuna") or {}
    if not a.get("enabled", True):
        raise Skip("disabled in config")
    if not (a.get("app_id") and a.get("app_key")):
        raise Skip("ADZUNA_APP_ID / ADZUNA_APP_KEY not set in config/secrets.env")

    country = str(a.get("country") or "us").lower()
    base = {
        "app_id": a["app_id"], "app_key": a["app_key"],
        "results_per_page": int(a.get("results_per_page", 50)),
        "max_days_old": int(s.get("max_days_old", 7)),
        "sort_by": "date", "content-type": "application/json",
    }
    passes = [{"what": "remote"}]
    if _metro(cfg):
        passes.append({"where": _metro(cfg), "distance": int(a.get("distance_km", 50))})
    budget = int(a.get("max_calls", 60))

    calls = 0
    seen: dict[str, dict] = {}
    for q in search_queries(cfg):
        for extra in passes:
            if calls >= budget:
                print(f"[warn] adzuna: stopped at max_calls={budget}; queries after '{q}' were not run")
                return [_adzuna_job(j, country) for j in seen.values()]
            calls += 1
            try:
                data = _get(ADZUNA_URL.format(country=country), params={**base, "title_only": q, **extra}) or {}
            except requests.RequestException as e:
                _failed("adzuna", q, e)
                continue
            for j in data.get("results") or []:
                jid = str(j.get("id") or "")
                if jid and jid not in seen:
                    seen[jid] = j
    return [_adzuna_job(j, country) for j in seen.values()]


# ------------------------------------------------------------------- usajobs
def _usajobs_public(d: dict) -> bool:
    """False when the announcement names hiring paths and the public is not one of them."""
    paths = ((d.get("UserArea") or {}).get("Details") or {}).get("HiringPath")
    if not paths:
        return True
    if not isinstance(paths, list):
        paths = [paths]
    codes = [str(p.get("Code", "") if isinstance(p, dict) else p).lower() for p in paths]
    return "public" in codes


def _usajobs_location(d: dict, metro: str) -> str:
    details = (d.get("UserArea") or {}).get("Details") or {}
    if str(details.get("RemoteIndicator")).lower() == "true":
        return "Remote, United States"
    locs = [x for x in d.get("PositionLocation") or [] if isinstance(x, dict)]
    names = [x.get("LocationName", "") for x in locs]
    local = [n for n in names if metro and metro.lower() in n.lower()]
    place = local[0] if local else (d.get("PositionLocationDisplay") or (names[0] if names else ""))
    domestic = all(x.get("CountryCode") in (None, "", "United States") for x in locs)
    return f"{place}, United States" if place and domestic else place


def _usajobs_job(pid: str, d: dict, metro: str) -> dict:
    details = (d.get("UserArea") or {}).get("Details") or {}
    duties = details.get("MajorDuties") or []
    if isinstance(duties, str):
        duties = [duties]
    facts = [
        f"Schedule: {', '.join(x.get('Name', '') for x in d.get('PositionSchedule') or [] if isinstance(x, dict))}.",
        f"Appointment: {', '.join(x.get('Name', '') for x in d.get('PositionOfferingType') or [] if isinstance(x, dict))}.",
        f"Telework eligible: {details.get('TeleworkEligible')}.",
        f"Security clearance: {details.get('SecurityClearance') or 'not stated'}.",
    ]
    text = " ".join([details.get("JobSummary") or "", *duties, d.get("QualificationSummary") or "", *facts])
    return {
        "source": "usajobs",
        "source_job_id": pid,
        "company": (d.get("OrganizationName") or d.get("DepartmentName") or "US federal government").strip(),
        "title": d.get("PositionTitle", ""),
        "location": _usajobs_location(d, metro),
        "url": d.get("PositionURI", ""),
        "description": _strip_html(text),
        "raw": d,
    }


def usajobs(cfg: dict) -> list[dict]:
    """One keyword call per query. The API wants the key holder's email as the User-Agent."""
    s = cfg.get("search") or {}
    u = s.get("usajobs") or {}
    if not u.get("enabled", True):
        raise Skip("disabled in config")
    if not (u.get("api_key") and u.get("email")):
        raise Skip("USAJOBS_API_KEY / USAJOBS_EMAIL not set in config/secrets.env")

    headers = {"Host": "data.usajobs.gov", "User-Agent": u["email"], "Authorization-Key": u["api_key"]}
    params = {"ResultsPerPage": int(u.get("results_per_page", 100)),
              "DatePosted": max(0, min(60, int(s.get("max_days_old", 7))))}
    seen: dict[str, dict] = {}
    for q in search_queries(cfg):
        try:
            data = _get(USAJOBS_URL, params={**params, "Keyword": q}, headers=headers) or {}
        except requests.RequestException as e:
            _failed("usajobs", q, e)
            continue
        for item in (data.get("SearchResult") or {}).get("SearchResultItems") or []:
            d = item.get("MatchedObjectDescriptor") or {}
            pid = str(item.get("MatchedObjectId") or d.get("PositionID") or "")
            if pid and pid not in seen:
                seen[pid] = d

    metro = _metro(cfg)
    return [_usajobs_job(pid, d, metro) for pid, d in seen.items()
            if not u.get("public_only", True) or _usajobs_public(d)]


# Run order used by the sourcing driver.
SOURCES: dict[str, Callable[[dict], list[dict]]] = {
    "adzuna": adzuna,
    "usajobs": usajobs,
}
