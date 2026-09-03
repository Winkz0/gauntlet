"""
Board adapters: pull job postings from public ATS (applicant tracking system)
JSON endpoints. No HTML scraping, no logins.

Each adapter takes the registry entry (a dict) and returns a list of
normalized dicts:
    {
      "source": "greenhouse",
      "source_job_id": "...",
      "company": "Rapid7",
      "title": "...",
      "location": "...",
      "url": "...",
      "description": "...",       # plain text
      "raw": {...},               # original payload
    }

Salary / remote_type / employment_type are parsed downstream in enrich.py,
because the heuristics are shared across all sources.

Registry fields per board type (adapters/registry.yaml):
    greenhouse       slug
    lever            slug
    ashby            slug
    smartrecruiters  slug
    workday          host, tenant, site        e.g. crowdstrike.wd5.myworkdayjobs.com / crowdstrike / crowdstrikecareers
    eightfold        host, domain              e.g. explore.jobs.netflix.net / netflix.com
    amazon           (none)                    amazon.jobs search endpoint

Workday, Eightfold, and Amazon need one request per posting to get the full
description, so they accept `search_terms` (a list of strings) to narrow the
listing first. Default terms live in DEFAULT_SEARCH_TERMS.
"""
from __future__ import annotations

import html
import re
import time
from typing import Any, Callable

import requests

UA = "gauntlet/1.0 (personal job search; contact: local)"
TIMEOUT = 20
DETAIL_SLEEP = 0.4          # polite pause between per-posting detail fetches
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept": "application/json"})

# Optional hook set by the sourcing driver: given a listing title, return
# False to skip the per-posting detail fetch. Search-driven boards return
# hundreds of listings and most fail the gate on title alone, so this cuts
# the run from many minutes to under one. Skipped listings come back as
# stubs with "prefiltered": True so the driver can keep an already-stored
# job marked live without storing new ones.
TITLE_FILTER: Callable[[str], bool] | None = None


def _stub(source: str, source_job_id: str, company: str, title: str, location: str, url: str) -> dict:
    return {"source": source, "source_job_id": source_job_id, "company": company,
            "title": title, "location": location, "url": url, "description": "",
            "raw": {}, "prefiltered": True}

# Used by boards that need a search query to keep the listing small.
DEFAULT_SEARCH_TERMS = [
    "security analyst", "incident response", "threat", "detection engineer",
    "security engineer", "soc", "forensic",
]


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _request(method: str, url: str, **kw) -> Any:
    for attempt in range(3):
        try:
            r = SESSION.request(method, url, timeout=TIMEOUT, **kw)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))


def _get(url: str, **kw) -> Any:
    return _request("GET", url, **kw)


def _post(url: str, payload: dict, **kw) -> Any:
    return _request("POST", url, json=payload, **kw)


# ---------------------------------------------------------------- greenhouse
def greenhouse(entry: dict) -> list[dict]:
    slug, company = entry["slug"], entry["name"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    data = _get(url) or {}
    out = []
    for j in data.get("jobs", []):
        out.append({
            "source": "greenhouse",
            "source_job_id": str(j.get("id")),
            "company": company,
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "description": _strip_html(j.get("content")),
            "raw": j,
        })
    return out


# --------------------------------------------------------------------- lever
def lever(entry: dict) -> list[dict]:
    slug, company = entry["slug"], entry["name"]
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = _get(url) or []
    out = []
    for j in data:
        cats = j.get("categories", {}) or {}
        out.append({
            "source": "lever",
            "source_job_id": j.get("id", ""),
            "company": company,
            "title": j.get("text", ""),
            "location": cats.get("location", ""),
            "url": j.get("hostedUrl", ""),
            "description": _strip_html(j.get("descriptionPlain") or j.get("description")),
            "raw": j,
        })
    return out


# --------------------------------------------------------------------- ashby
def ashby(entry: dict) -> list[dict]:
    slug, company = entry["slug"], entry["name"]
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    data = _get(url) or {}
    out = []
    for j in data.get("jobs", []):
        out.append({
            "source": "ashby",
            "source_job_id": j.get("id", ""),
            "company": company,
            "title": j.get("title", ""),
            "location": j.get("location", ""),
            "url": j.get("jobUrl", ""),
            "description": _strip_html(j.get("descriptionHtml") or j.get("descriptionPlain")),
            "raw": j,
        })
    return out


# ------------------------------------------------------------- smartrecruiters
def smartrecruiters(entry: dict) -> list[dict]:
    slug, company = entry["slug"], entry["name"]
    base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    data = _get(base) or {}
    out = []
    for j in data.get("content", []):
        loc = j.get("location", {}) or {}
        loc_str = ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")]))
        out.append({
            "source": "smartrecruiters",
            "source_job_id": j.get("id", ""),
            "company": company,
            "title": j.get("name", ""),
            "location": loc_str,
            "url": (j.get("ref") or {}).get("jobAd", "") or
                   f"https://jobs.smartrecruiters.com/{slug}/{j.get('id','')}",
            "description": "",  # SmartRecruiters needs a per-posting fetch for the full text
            "raw": j,
        })
    return out


# ------------------------------------------------------------------- workday
def workday(entry: dict) -> list[dict]:
    """
    Workday "CXS" JSON API used by every *.myworkdayjobs.com career site.

    List:   POST https://{host}/wday/cxs/{tenant}/{site}/jobs
            {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "..."}
    Detail: GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}

    The list call is paginated (limit max 20) and only returns title,
    location text, and the path. We run one list query per search term,
    dedupe on the requisition path, then fetch details for each posting.
    """
    host, tenant, site, company = entry["host"], entry["tenant"], entry["site"], entry["name"]
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    terms = entry.get("search_terms") or DEFAULT_SEARCH_TERMS
    max_per_term = int(entry.get("max_per_term", 100))

    paths: dict[str, dict] = {}
    for term in terms:
        offset = 0
        while offset < max_per_term:
            data = _post(f"{base}/jobs", {
                "appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term,
            }) or {}
            postings = data.get("jobPostings", []) or []
            for p in postings:
                path = p.get("externalPath")
                if path and path not in paths:
                    paths[path] = p
            if len(postings) < 20:
                break
            offset += 20

    out = []
    for path, p in paths.items():
        if TITLE_FILTER and not TITLE_FILTER(p.get("title", "")):
            out.append(_stub("workday", (p.get("bulletFields") or [""])[0] or path, company,
                             p.get("title", ""), p.get("locationsText", ""), f"https://{host}/{site}{path}"))
            continue
        try:
            info = (_get(f"{base}{path}") or {}).get("jobPostingInfo", {}) or {}
        except requests.RequestException as e:
            print(f"[warn] workday detail failed {company} {path}: {e}")
            info = {}
        time.sleep(DETAIL_SLEEP)
        out.append({
            "source": "workday",
            "source_job_id": info.get("jobReqId") or (p.get("bulletFields") or [""])[0] or path,
            "company": company,
            "title": info.get("title") or p.get("title", ""),
            "location": info.get("location") or p.get("locationsText", ""),
            "url": info.get("externalUrl") or f"https://{host}/{site}{path}",
            "description": _strip_html(info.get("jobDescription")),
            "raw": {"listing": p, "detail": {k: v for k, v in info.items() if k != "jobDescription"}},
        })
    return out


# ----------------------------------------------------------------- eightfold
def eightfold(entry: dict) -> list[dict]:
    """
    Eightfold career sites (Netflix, and many others) expose:
      GET https://{host}/api/apply/v2/jobs?domain={domain}&query=...&num=...&start=...
      GET https://{host}/api/apply/v2/jobs/{id}?domain={domain}
    The list omits the description, so we fetch each posting once.
    """
    host, domain, company = entry["host"], entry["domain"], entry["name"]
    terms = entry.get("search_terms") or DEFAULT_SEARCH_TERMS
    max_per_term = int(entry.get("max_per_term", 100))

    seen: dict[str, dict] = {}
    for term in terms:
        start = 0
        while start < max_per_term:
            data = _get(f"https://{host}/api/apply/v2/jobs",
                        params={"domain": domain, "query": term, "num": 20, "start": start}) or {}
            positions = data.get("positions", []) or []
            for p in positions:
                pid = str(p.get("id"))
                if pid and pid not in seen:
                    seen[pid] = p
            if len(positions) < 20:
                break
            start += 20

    out = []
    for pid, p in seen.items():
        if TITLE_FILTER and not TITLE_FILTER(p.get("name", "")):
            out.append(_stub("eightfold", pid, company, p.get("name", ""), p.get("location", ""),
                             p.get("canonicalPositionUrl", "")))
            continue
        try:
            detail = _get(f"https://{host}/api/apply/v2/jobs/{pid}", params={"domain": domain}) or {}
        except requests.RequestException as e:
            print(f"[warn] eightfold detail failed {company} {pid}: {e}")
            detail = {}
        time.sleep(DETAIL_SLEEP)
        out.append({
            "source": "eightfold",
            "source_job_id": pid,
            "company": company,
            "title": detail.get("name") or p.get("name", ""),
            "location": detail.get("location") or p.get("location", ""),
            "url": detail.get("canonicalPositionUrl") or p.get("canonicalPositionUrl", ""),
            "description": _strip_html(detail.get("job_description")),
            "raw": {k: v for k, v in (detail or p).items() if k != "job_description"},
        })
    return out


# -------------------------------------------------------------------- amazon
def amazon(entry: dict) -> list[dict]:
    """
    amazon.jobs has a JSON search endpoint that already includes the
    description and qualifications, so no detail fetch is needed.
      GET https://www.amazon.jobs/en/search.json?base_query=...&result_limit=100&offset=0
    """
    company = entry["name"]
    terms = entry.get("search_terms") or DEFAULT_SEARCH_TERMS
    max_per_term = int(entry.get("max_per_term", 100))

    seen: dict[str, dict] = {}
    for term in terms:
        offset = 0
        while offset < max_per_term:
            data = _get("https://www.amazon.jobs/en/search.json", params={
                "base_query": term, "loc_query": entry.get("loc_query", ""),
                "result_limit": 100, "offset": offset,
            }) or {}
            jobs = data.get("jobs", []) or []
            for j in jobs:
                jid = str(j.get("id") or j.get("id_icims"))
                if jid and jid not in seen:
                    seen[jid] = j
            if len(jobs) < 100:
                break
            offset += 100

    out = []
    for jid, j in seen.items():
        desc = " ".join(filter(None, [
            j.get("description"), "Basic qualifications:", j.get("basic_qualifications"),
            "Preferred qualifications:", j.get("preferred_qualifications"),
        ]))
        out.append({
            "source": "amazon",
            "source_job_id": jid,
            "company": company,
            "title": j.get("title", ""),
            "location": j.get("normalized_location") or j.get("location", ""),
            "url": "https://www.amazon.jobs" + (j.get("job_path") or ""),
            "description": _strip_html(desc),
            "raw": {k: v for k, v in j.items()
                    if k not in ("description", "basic_qualifications", "preferred_qualifications")},
        })
    return out


# Dispatch table used by the sourcing driver.
ADAPTERS: dict[str, Callable[[dict], list[dict]]] = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workday": workday,
    "eightfold": eightfold,
    "amazon": amazon,
}

REQUIRED_FIELDS = {
    "greenhouse": ("slug",), "lever": ("slug",), "ashby": ("slug",),
    "smartrecruiters": ("slug",), "workday": ("host", "tenant", "site"),
    "eightfold": ("host", "domain"), "amazon": (),
}


def validate_entry(entry: dict) -> str | None:
    """Return a human-readable problem with a registry entry, or None if usable."""
    board = entry.get("board")
    if board not in ADAPTERS:
        return f"board '{board}' has no adapter"
    missing = [f for f in REQUIRED_FIELDS[board] if not entry.get(f)]
    if missing:
        return f"missing registry fields for {board}: {', '.join(missing)}"
    return None


def pull(entry: dict) -> list[dict]:
    """Dispatch a registry entry to its adapter. One company failing never halts sourcing."""
    problem = validate_entry(entry)
    if problem:
        print(f"[skip] {entry.get('name')}: {problem}")
        return []
    try:
        return ADAPTERS[entry["board"]](entry)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] {entry['board']}:{entry.get('name')} failed: {e}")
        return []
