"""
Salary fallback for postings with no listed comp.

Strategy (cheapest first):
  1. Geo-average table (static, editable) keyed on role family + metro.
     Zero network, instant, good enough to gate on with an "estimate" tag.
  2. (Optional) live lookup hook, left as a stub you can wire to a
     levels.fyi / Glassdoor source you trust. Off by default to avoid
     scraping ToS headaches.

Anything estimated is tagged salary_source="geo_average" (or "osint_estimate")
so the filter + digest can label it "est., unverified" and never silently passed.
"""
from __future__ import annotations
import re

# Rough Chicago-metro base ranges for common security roles (annual USD).
# The metro label comes from filters.location_context; the numbers are yours to edit.
# Edit these to match your own read of the market; they are your gate, not gospel.
GEO_AVERAGES = {
    "soc_analyst":          (95_000, 125_000),
    "security_analyst":     (100_000, 130_000),
    "incident_response":    (115_000, 150_000),
    "dfir":                 (120_000, 160_000),
    "threat_hunt":          (120_000, 155_000),
    "detection_engineer":   (125_000, 165_000),
    "cloud_security":       (125_000, 165_000),
    "security_engineer":    (120_000, 160_000),
    "penetration_test":     (120_000, 160_000),
    "red_team":             (130_000, 175_000),
}

ROLE_PATTERNS = [
    (r"detection engineer", "detection_engineer"),
    (r"red team", "red_team"),
    (r"pen(etration)? test|offensive security", "penetration_test"),
    (r"cloud security", "cloud_security"),
    (r"threat hunt", "threat_hunt"),
    (r"dfir|forensic", "dfir"),
    (r"incident response|\bir\b", "incident_response"),
    (r"security engineer", "security_engineer"),
    (r"soc analyst", "soc_analyst"),
    (r"security analyst", "security_analyst"),
]


def classify_role(title: str, desc: str = "") -> str | None:
    blob = f"{title} {desc}".lower()
    for rx, fam in ROLE_PATTERNS:
        if re.search(rx, blob):
            return fam
    return None


def estimate_salary(job: dict, metro: str = "Chicago") -> dict:
    metro = (metro or "Chicago").split(",")[0].strip()
    """Fill salary_min/max with a geo estimate if none present. Mutates + returns."""
    if job.get("salary_max") is not None:
        return job  # already have real numbers

    fam = classify_role(job.get("title", ""), job.get("description", ""))
    table = GEO_AVERAGES  # one table for now; keyed on role family, labelled with the metro
    if fam and fam in table:
        lo, hi = table[fam]
        job["salary_min"] = lo
        job["salary_max"] = hi
        job["salary_source"] = "geo_average"
        job["salary_note"] = f"est., unverified: {metro} avg for {fam.replace('_', ' ')}"
    return job


def live_lookup_stub(company: str, title: str, metro: str) -> tuple[int, int] | None:
    """
    Placeholder for a live OSINT source. Wire to a source you're comfortable
    with (API, not scrape). Return (min, max) or None.
    """
    return None
