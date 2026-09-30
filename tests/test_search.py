import json

import pytest
import requests

from adapters import search as SR
from pipeline import enrich as E
from pipeline import filters as F
from pipeline import source
from pipeline.digest import render_md
from tests.conftest import make_job

ADZUNA_RESULT = {
    "id": "4855123456",
    "title": "<strong>SOC</strong> <strong>Analyst</strong> II (Remote)",
    "description": "Monitor alerts and triage incidents for our <strong>SOC</strong>...",
    "company": {"display_name": "Example Corp"},
    "location": {"display_name": "Austin, Travis County", "area": ["US", "Texas", "Travis County", "Austin"]},
    "redirect_url": "https://www.adzuna.com/land/ad/4855123456",
    "salary_min": 118000.0, "salary_max": 138000.0, "salary_is_predicted": "0",
    "contract_type": "permanent", "contract_time": "full_time",
}


def _usajobs_item(pid, title="Cyber Threat Intelligence Analyst", paths=("public",), remote=False, locs=None):
    return {"MatchedObjectId": pid, "MatchedObjectDescriptor": {
        "PositionID": f"CISA-{pid}", "PositionTitle": title,
        "PositionURI": f"https://www.usajobs.gov/job/{pid}",
        "PositionLocationDisplay": "Multiple Locations",
        "PositionLocation": locs if locs is not None else [
            {"LocationName": "Arlington, Virginia", "CountryCode": "United States"},
            {"LocationName": "Chicago, Illinois", "CountryCode": "United States"}],
        "OrganizationName": "Cybersecurity and Infrastructure Security Agency",
        "PositionSchedule": [{"Name": "Full-time", "Code": "1"}],
        "PositionOfferingType": [{"Name": "Permanent", "Code": "15317"}],
        "QualificationSummary": "Experience producing threat intelligence.",
        "PositionRemuneration": [{"MinimumRange": "117962.0", "MaximumRange": "153354.0",
                                  "RateIntervalCode": "PA", "Description": "Per Year"}],
        "UserArea": {"Details": {"JobSummary": "Analyze adversary activity.",
                                 "MajorDuties": ["Produce threat intelligence reporting."],
                                 "HiringPath": list(paths), "TeleworkEligible": True,
                                 "RemoteIndicator": remote, "SecurityClearance": "Top Secret"}}}}


@pytest.fixture
def keyed(cfg):
    cfg["search"]["adzuna"].update(app_id="id", app_key="SECRETKEY", max_calls=60)
    cfg["search"]["usajobs"].update(api_key="ukey", email="me@example.com")
    return cfg


def test_search_queries_follow_config_without_repeats(cfg):
    qs = SR.search_queries(cfg)
    assert qs[0] == cfg["role_families"]["soc_analyst"]["queries"][0]
    assert len(qs) == len({q.lower() for q in qs})
    cfg["role_families"]["extra"] = {"queries": ["SOC Analyst", "brand new query"]}
    assert SR.search_queries(cfg)[-2:] == [qs[-1], "brand new query"]


def test_sources_skip_without_keys(cfg):
    for block in ("adzuna", "usajobs"):
        for k in ("app_id", "app_key", "api_key", "email"):
            cfg["search"][block].pop(k, None)
    for fetch in SR.SOURCES.values():
        with pytest.raises(SR.Skip):
            fetch(cfg)


def test_adzuna_normalizes_and_dedupes(keyed, monkeypatch):
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params)
        return {"results": [ADZUNA_RESULT]}
    monkeypatch.setattr(SR, "_get", fake_get)
    jobs = SR.adzuna(keyed)
    assert len(jobs) == 1                                  # the same id comes back from every call
    assert len(calls) == 2 * len(SR.search_queries(keyed))
    assert calls[0]["what"] == "remote" and calls[1]["where"] == "Chicago"
    assert calls[0]["title_only"] == SR.search_queries(keyed)[0]
    job = jobs[0]
    assert job["title"] == "SOC Analyst II (Remote)"
    assert job["company"] == "Example Corp"
    assert job["location"] == "Austin, Travis County, US"
    E.enrich(job)
    assert (job["salary_min"], job["salary_max"], job["salary_source"]) == (118000, 138000, "posting")
    assert job["remote_type"] == "remote" and job["employment_type"] == "direct"
    ok, reasons = F.apply_filters(job, keyed)
    assert ok and "general_search" in F.tags_from_reasons(reasons)


def test_adzuna_stops_at_its_call_budget(keyed, monkeypatch, capsys):
    keyed["search"]["adzuna"]["max_calls"] = 3
    calls = []
    monkeypatch.setattr(SR, "_get", lambda url, params=None, **kw: calls.append(params) or {"results": []})
    SR.adzuna(keyed)
    assert len(calls) == 3
    assert "max_calls=3" in capsys.readouterr().out


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(
        f"{status} Error for url: https://api.adzuna.com/v1/api/jobs/us/search/1?app_id=id&app_key=SECRETKEY",
        response=resp)


def test_adzuna_rejected_key_stops_the_source(keyed, monkeypatch):
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        raise _http_error(401)
    monkeypatch.setattr(SR, "_get", fake_get)
    with pytest.raises(SR.Skip) as e:
        SR.adzuna(keyed)
    assert len(calls) == 1 and "SECRETKEY" not in str(e.value)


def test_failed_query_warning_hides_the_key(keyed, monkeypatch, capsys):
    def fake_get(*a, **k):
        raise _http_error(500)
    monkeypatch.setattr(SR, "_get", fake_get)
    assert SR.adzuna(keyed) == []
    out = capsys.readouterr().out
    assert "[warn] adzuna" in out and "SECRETKEY" not in out


def test_usajobs_normalizes_and_drops_status_only(keyed, monkeypatch):
    seen_headers = []

    def fake_get(url, params=None, headers=None, **kw):
        seen_headers.append(headers)
        return {"SearchResult": {"SearchResultItems": [
            _usajobs_item("1"), _usajobs_item("2", paths=("fed-competitive",))]}}
    monkeypatch.setattr(SR, "_get", fake_get)
    jobs = SR.usajobs(keyed)
    assert seen_headers[0]["Authorization-Key"] == "ukey"
    assert seen_headers[0]["User-Agent"] == "me@example.com"
    assert [j["source_job_id"] for j in jobs] == ["1"]
    job = jobs[0]
    assert job["location"] == "Chicago, Illinois, United States"      # the metro wins among several
    assert job["company"] == "Cybersecurity and Infrastructure Security Agency"
    E.enrich(job)
    assert (job["salary_min"], job["salary_max"], job["salary_source"]) == (117962, 153354, "posting")
    assert job["employment_type"] == "direct"


def test_usajobs_locations():
    remote = _usajobs_item("3", remote=True)["MatchedObjectDescriptor"]
    assert SR._usajobs_location(remote, "Chicago") == "Remote, United States"
    abroad = _usajobs_item("4", locs=[{"LocationName": "Ramstein AB, Germany", "CountryCode": "Germany"}])
    d = abroad["MatchedObjectDescriptor"]
    d["PositionLocationDisplay"] = "Ramstein AB, Germany"
    assert SR._usajobs_location(d, "Chicago") == "Ramstein AB, Germany"


def test_canonical_company_matches_named_employers():
    named = ["Amazon", "Huntress", "Mizuho Americas", "Keeper Security", "Apple",
             "Federal Reserve Bank of Chicago"]
    assert source.canonical_company("Amazon Web Services, Inc.", named) == "Amazon"
    assert source.canonical_company("Amazon.com Services LLC", named) == "Amazon"
    assert source.canonical_company("Huntress Labs", named) == "Huntress"
    assert source.canonical_company("Mizuho Americas Services LLC", named) == "Mizuho Americas"
    assert source.canonical_company("KEEPER SECURITY, INC.", named) == "Keeper Security"
    assert source.canonical_company("Apple Leisure Group", named) == "Apple Leisure Group"
    assert source.canonical_company("Keeper", named) == "Keeper"
    assert source.canonical_company("Federal Reserve Bank of New York", named) == "Federal Reserve Bank of New York"


def test_named_companies_join_registry_and_targets(cfg):
    names = source.named_companies(cfg, {"companies": [{"name": "WiredCo"}, {"name": "CrowdStrike"}]})
    assert "WiredCo" in names and "Rapid7" in names and names.count("CrowdStrike") == 1


def test_run_search_routes_results(cfg, con, monkeypatch, capsys):
    reg = {"companies": [{"name": "WiredCo", "board": "greenhouse", "slug": "wiredco"},
                         {"name": "Rapid7", "board": "unsupported"}]}
    cfg["named_companies"] = source.named_companies(cfg, reg)
    pasted, _ = source.store.upsert_job(con, make_job(
        source="manual_paste", source_job_id="m1", company="Pasted Co", title="Threat Hunter II",
        description="The full posting the human pasted."))
    results = [
        make_job(source="adzuna", source_job_id="a1", company="WiredCo, Inc.", title="SOC Analyst II"),
        make_job(source="adzuna", source_job_id="a2", company="Rapid7 LLC",
                 title="Threat Intelligence Analyst II", description="Full-time."),
        make_job(source="adzuna", source_job_id="a3", company="Unlisted Co", title="Detection Engineer II",
                 description="Full-time. $130,000 - $160,000"),
        make_job(source="adzuna", source_job_id="a4", company="Unlisted Co", title="Payroll Specialist"),
        make_job(source="adzuna", source_job_id="a5", company="Pasted Co", title="Threat Hunter II",
                 description="snippet"),
    ]
    monkeypatch.setattr(source.SR, "SOURCES", {"adzuna": lambda c: [dict(j) for j in results]})
    totals = {"seen": 0, "new": 0, "passed": 0, "gone": 0}
    source.run_search(con, cfg, reg, totals)

    rows = {r["company"]: r for r in con.execute(
        "SELECT j.*, fr.passed, fr.reasons FROM jobs j JOIN filter_results fr ON fr.job_id = j.id")}
    assert set(rows) == {"Rapid7", "Unlisted Co"}                 # wired dropped, payroll never stored
    assert rows["Rapid7"]["passed"] and "preferred" in F.tags_from_reasons(rows["Rapid7"]["reasons"])
    assert rows["Unlisted Co"]["passed"]
    assert "general_search" in F.tags_from_reasons(rows["Unlisted Co"]["reasons"])
    kept = con.execute("SELECT source, description FROM jobs WHERE id=?", (pasted,)).fetchone()
    assert tuple(kept) == ("manual_paste", "The full posting the human pasted.")
    assert totals["new"] == 2 and totals["passed"] == 2
    assert "[ok]   search adzuna: 5 results, 1 at registry companies" in capsys.readouterr().out


def _digest_row(i, company, tags):
    return {"id": i, "title": "SOC Analyst II", "company": company, "location": "Remote",
            "remote_type": "remote", "salary_min": 120000, "salary_max": 150000,
            "salary_source": "posting", "employment_type": "direct", "url": "https://example.com",
            "reasons": json.dumps([{"rule": "x", "ok": True, "tag": t} for t in tags])}


def test_digest_lists_named_companies_before_general_search():
    md = render_md([_digest_row(1, "Unlisted Co", ["general_search"]), _digest_row(2, "Rapid7", ["preferred"])])
    assert md.index("## Named companies (1)") < md.index("Rapid7") \
        < md.index("## General search (1)") < md.index("Unlisted Co")
