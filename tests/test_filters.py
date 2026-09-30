from pipeline import filters as F
from tests.conftest import make_job


def _run(cfg, **over):
    job = make_job(**over)
    from pipeline import enrich as E
    E.enrich(job)
    return F.apply_filters(job, cfg)


def test_clean_pass(cfg):
    ok, reasons = _run(cfg)
    assert ok
    assert "strong" in F.tags_from_reasons(reasons)


def test_body_keyword_needs_security_title(cfg):
    ok, reasons = _run(cfg, title="Senior DevOps Engineer",
                       description="You will partner with the security engineer team. Full-time.")
    assert not ok
    ok, reasons = _run(cfg, title="Senior Information Systems Security Officer",
                       description="Support incident response and threat hunting. Full-time.")
    assert ok
    assert "verify_role" in F.tags_from_reasons(reasons)


def test_blocklist_is_title_only(cfg):
    ok, _ = _run(cfg, title="Account Manager, Security", description="incident response")
    assert not ok
    ok, _ = _run(cfg, title="SOC Analyst", description="works with the sales team")
    assert ok


def test_hybrid_outside_metro_fails(cfg):
    ok, reasons = _run(cfg, location="County Cork, Ireland", description="Hybrid. Full-time. incident response")
    assert not ok
    ok, _ = _run(cfg, location="Chicago, IL (Hybrid)", description="Full-time.")
    assert ok


def test_geo_strict_and_bare_remote(cfg):
    ok, reasons = _run(cfg, location="London, England, GBR")
    assert not ok
    ok, reasons = _run(cfg, location="Remote")
    assert ok and "verify_location" in F.tags_from_reasons(reasons)
    ok, reasons = _run(cfg, location="USA - Remote")
    assert ok and "verify_location" not in F.tags_from_reasons(reasons)
    ok, _ = _run(cfg, location="Brazil - Remote")
    assert not ok


def test_unknown_mode_named_city_outside_metro_fails(cfg):
    ok, _ = _run(cfg, location="Seattle, Washington, USA", description="Full-time. $150,000 - $200,000")
    assert not ok
    ok, reasons = _run(cfg, location="USA", description="Full-time. $150,000 - $200,000")
    assert ok and "verify_remote" in F.tags_from_reasons(reasons)
    ok, _ = _run(cfg, location="Chicago, Illinois", description="Full-time. $150,000 - $200,000")
    assert ok


def test_geo_block(cfg):
    ok, reasons = _run(cfg, location="Bengaluru, India")
    assert not ok
    assert any(r["rule"] == "geo" and not r["ok"] for r in reasons)


def test_salary_floor(cfg):
    ok, _ = _run(cfg, description="Full-time. $80,000 - $95,000")
    assert not ok


def test_contract_blocked(cfg):
    ok, _ = _run(cfg, description="This is a 12-month contract position. $120,000 - $150,000")
    assert not ok


def test_tags_from_reasons_tolerates_legacy_strings():
    assert F.tags_from_reasons('["remote: ok", "salary: not stated"]') == set()
    assert F.tags_from_reasons([{"rule": "x", "ok": True, "tag": "strong"}, "legacy"]) == {"strong"}


def test_title_prefilter_mirrors_title_rules(cfg):
    assert F.title_prefilter("Senior Threat Hunter", cfg)
    assert F.title_prefilter("Information Systems Security Officer", cfg)      # gate term only
    assert not F.title_prefilter("Senior DevOps Engineer", cfg)
    assert not F.title_prefilter("Security Sales Engineer", cfg)                # blocked
    assert not F.title_prefilter("Sr. Analyst, Sales Support", cfg)


def _capped(cfg):
    cfg["filters"]["level_cap"] = {"companies": ["BigCo"], "floor": "analyst_i", "ceiling": "analyst_ii",
                                   "max_level_number": 4, "tag_unlabeled": True}
    return cfg


def test_level_cap_ceiling_and_ladder_numbers(cfg):
    cfg = _capped(cfg)
    for title in ("Security Engineer (L5) - Cloud Architecture", "Security Engineer 5 - IAM",
                  "Senior Security Engineer", "Staff Security Engineer", "Lead Threat Hunter"):
        ok, reasons = _run(cfg, company="BigCo", title=title)
        assert not ok, title
        assert any(r["rule"] == "level_cap" and not r["ok"] for r in reasons), title
    for title in ("Security Engineer II, Security Incident Response Team", "Security Engineer (L4)"):
        ok, _ = _run(cfg, company="BigCo", title=title)
        assert ok, title


def test_level_cap_lowers_floor_only_for_its_companies(cfg):
    cfg = _capped(cfg)
    ok, _ = _run(cfg, company="BigCo", title="Security Engineer I, Threat Hunting")
    assert ok
    ok, _ = _run(cfg, company="Acme", title="Security Engineer I, Threat Hunting")
    assert not ok                                    # global floor still Analyst II
    ok, _ = _run(cfg, company="Acme", title="Senior Security Engineer")
    assert ok                                        # no ceiling outside the cap


def test_level_cap_tags_titles_without_a_level(cfg):
    ok, reasons = _run(_capped(cfg), company="BigCo", title="Threat Hunter")
    assert ok and "verify_level" in F.tags_from_reasons(reasons)


def test_appended_blocklist_drops_managers_keeps_leads(cfg):
    from pipeline.config import merge_local
    merge_local(cfg, {"role_keywords_block+": ["manager", "supervisor"]})
    ok, _ = _run(cfg, title="Security Engineering Manager - Incident Response")
    assert not ok
    ok, _ = _run(cfg, title="Senior Manager, Red Team")
    assert not ok
    ok, _ = _run(cfg, title="Lead Threat Hunter")
    assert ok
    assert not F.title_prefilter("SOC Supervisor", cfg)


def _floors(cfg):
    """The per-family floors a config.local.yaml would set."""
    from pipeline.config import merge_local
    merge_local(cfg, {"role_families": {
        "soc_analyst": {"min_level": "analyst_ii", "tag_unlabeled": True},
        "detection_engineer": {"min_level": "analyst_i"},
        "threat_intel": {"min_level": "analyst_i"}}})
    return cfg


def test_role_family_variants_match_in_any_order(cfg):
    for title in ("Analyst, SOC", "SOC Tier 2 Analyst", "Security Operations Center (SOC) Analyst II",
                  "Cyber Threat Intelligence Analyst", "Threat Intel Analyst", "CTI Analyst",
                  "Detection & Response Engineer", "Engineer, Threat Detection", "Detection Engineering Lead",
                  "Senior Incident Responder", "Malware Reverse Engineer"):
        assert F.role_families(title, cfg), title
        assert F.title_prefilter(title, cfg), title
    assert F.role_families("Cyber Threat Intelligence Analyst", cfg) == ["threat_intel"]
    assert not F.role_families("Business Intelligence Analyst", cfg)
    assert not F.role_families("Associate Director, Social Media", cfg)       # "soc" is whole-word


def test_family_floors(cfg):
    cfg = _floors(cfg)
    for title in ("Detection Engineer I", "Threat Intelligence Analyst I", "Detection Engineer",
                  "SOC Analyst II", "SOC Analyst (T2)", "Senior SOC Analyst"):
        ok, _ = _run(cfg, title=title)
        assert ok, title
    for title in ("SOC Analyst I", "SOC Analyst L1", "Analyst, SOC Tier 1", "Incident Response Analyst I"):
        ok, reasons = _run(cfg, title=title)
        assert not ok, title
    _, reasons = _run(cfg, title="SOC Analyst I")
    assert any(r["rule"] == "seniority" and "soc_analyst floor" in r["detail"] for r in reasons)


def test_family_floor_can_sit_above_the_global_floor(cfg):
    cfg["filters"]["seniority_min"] = "analyst_i"
    cfg["role_families"]["soc_analyst"]["min_level"] = "analyst_ii"
    ok, _ = _run(cfg, title="SOC Analyst I")
    assert not ok
    ok, _ = _run(cfg, title="Incident Response Analyst I")
    assert ok


def test_lowest_family_floor_wins(cfg):
    ok, _ = _run(_floors(cfg), title="SOC Detection Engineer I")
    assert ok


def test_level_cap_floor_outranks_family_floor(cfg):
    cfg = _capped(_floors(cfg))
    ok, _ = _run(cfg, company="BigCo", title="SOC Analyst I")
    assert ok


def test_unlabeled_soc_title_is_tagged(cfg):
    cfg = _floors(cfg)
    ok, reasons = _run(cfg, title="SOC Analyst")
    assert ok and "verify_level" in F.tags_from_reasons(reasons)
    for title in ("SOC Analyst II", "Detection Engineer"):
        ok, reasons = _run(cfg, title=title)
        assert ok and "verify_level" not in F.tags_from_reasons(reasons), title


def test_unnamed_company_needs_a_salary(cfg):
    ok, reasons = _run(cfg, company="Unlisted Co", description="Full-time.")
    assert not ok
    assert any(r["rule"] == "salary" and not r["ok"] for r in reasons)
    ok, reasons = _run(cfg, company="Unlisted Co", description="Full-time. $120,000 - $150,000")
    assert ok and "general_search" in F.tags_from_reasons(reasons)
    ok, _ = _run(cfg, company="Unlisted Co", description="Full-time. $80,000 - $95,000")
    assert not ok


def test_named_companies_and_manual_intakes_keep_unverified_salary(cfg):
    for over in ({"company": "Acme"}, {"company": "CrowdStrike"},
                 {"company": "Unlisted Co", "source": "manual_paste"}):
        ok, reasons = _run(cfg, description="Full-time.", **over)
        tags = F.tags_from_reasons(reasons)
        assert ok and "salary_unknown" in tags and "general_search" not in tags, over


def test_unnamed_company_passes_on_an_estimate(cfg):
    from pipeline import enrich as E
    from pipeline import salary_osint as S
    job = make_job(company="Unlisted Co", title="SOC Analyst II", description="Full-time.")
    E.enrich(job)
    S.estimate_salary(job, "Chicago")
    ok, reasons = F.apply_filters(job, cfg)
    assert ok and "estimate" in F.tags_from_reasons(reasons)
    ok, reasons = _run(cfg, company="Unlisted Co", description="Full-time.",
                       raw={"salary_is_predicted": "1", "salary_min": 105000, "salary_max": 135000})
    assert ok and "estimate" in F.tags_from_reasons(reasons)


def test_unnamed_salary_gate_can_be_turned_off(cfg):
    cfg["filters"]["unnamed_require_salary"] = False
    ok, reasons = _run(cfg, company="Unlisted Co", description="Full-time.")
    assert ok and {"salary_unknown", "general_search"} <= F.tags_from_reasons(reasons)


def test_staffing_firm_names_fail(cfg):
    for company in ("Beacon Hill", "TEKsystems, Inc.", "Motion Recruitment Partners", "Top Talent Staffing"):
        ok, reasons = _run(cfg, company=company)
        assert not ok, company
        assert any(r["rule"] == "employment" and "staffing firm" in r["detail"] for r in reasons), company
    for company in ("Beacon Health Options", "TalentLMS", "Acme"):        # whole-word only
        ok, _ = _run(cfg, company=company)
        assert ok, company
