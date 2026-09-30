from pipeline import enrich as E


def test_salary_needs_money_marker():
    assert E.parse_salary("100-150 employees") == (None, None, "none")
    assert E.parse_salary("2-3 years of experience") == (None, None, "none")
    assert E.parse_salary("$120,000 - $150,000") == (120000, 150000, "posting")
    assert E.parse_salary("120k-150k") == (120000, 150000, "posting")
    assert E.parse_salary("$95k to $110k") == (95000, 110000, "posting")
    assert E.parse_salary("USD 120,000 to 150,000") == (120000, 150000, "posting")


def test_salary_ignores_hourly_and_single_amounts_out_of_window():
    assert E.parse_salary("$50 - $60 per hour") == (None, None, "none")
    assert E.parse_salary("base salary of $135,000") == (135000, 135000, "posting")


def test_salary_ignores_401k_and_context_free_amounts():
    assert E.parse_salary("Benefits include a 401k match and 401(k) plan") == (None, None, "none")
    assert E.parse_salary("Up to $500,000 in bonus pool") == (None, None, "none")
    assert E.parse_salary("The base salary for this role is $135,000 per year") == (135000, 135000, "posting")


def test_salary_structured_ashby():
    raw = {"compensation": {"compensationTiers": [
        {"components": [{"minValue": 110000, "maxValue": 140000}]}]}}
    assert E.parse_salary("", raw) == (110000, 140000, "posting")


def test_remote_location_wins_over_body():
    body = "This is a 100% remote position with an opportunity to work a hybrid schedule."
    assert E.parse_remote(body, "Remote, US") == "remote"
    assert E.parse_remote("hybrid 3 days in office", "Chicago, IL") == "hybrid"
    assert E.parse_remote("", "Chicago, IL (Hybrid)") == "hybrid"
    assert E.parse_remote("must be on-site daily", "Chicago, IL") == "onsite"
    assert E.parse_remote("", "3 Locations") == "unknown"


def test_employment_contract_needs_employment_phrase():
    assert E.parse_employment("Work with clients to negotiate contract renewals") == "unknown"
    assert E.parse_employment("This is a 6-month contract position") == "contract"
    assert E.parse_employment("Our client is seeking a SOC analyst") == "staffing"
    assert E.parse_employment("", {"detail": {"timeType": "Full time"}}) == "direct"
    assert E.parse_employment("", {"job_schedule_type": "full-time"}) == "direct"


def test_seniority():
    assert E.parse_seniority("Senior Threat Hunter") == "senior"
    assert E.parse_seniority("SOC Analyst II") == "analyst_ii"
    assert E.parse_seniority("Security Analyst I") == "analyst_i"
    assert E.parse_seniority("Director, Cyber Defense") == "principal"
    assert E.parse_seniority("Threat Detection Engineer") == "analyst_ii"


def test_level_number_from_ladder_tokens():
    assert E.parse_level_number("Security Engineer (L5) - Cloud Architecture") == 5
    assert E.parse_level_number("Business Security Partner (L5)") == 5
    assert E.parse_level_number("Security Engineer 5 - IAM") == 5
    assert E.parse_level_number("Security Engineer, E4") == 4
    assert E.parse_level_number("ICT3 Security Analyst") == 3
    assert E.parse_level_number("Security Engineer II, SIRT") is None
    assert E.parse_level_number("E2E Test Engineer") is None


def test_level_signal_distinguishes_default_seniority():
    assert E.has_level_signal("Security Engineer I, Threat Hunting")
    assert E.has_level_signal("Security Engineer 5 - IAM")
    assert E.has_level_signal("Senior Threat Hunter")
    assert not E.has_level_signal("Network Security Penetration Tester, AppSTAR")


def test_salary_per_year_suffix_counts_as_context():
    # Geo-banded ranges are often written as two amounts far apart, each tagged "/year".
    text = ("The US base salary for this position ranges from $94,000/year in our lowest geographic "
            "market up to $140,000/year in our highest geographic market.")
    assert E.parse_salary(text) == (94000, 140000, "posting")
    assert E.parse_salary("Pay: $135,000/yr plus bonus") == (135000, 135000, "posting")


def test_salary_structured_search_sources():
    adzuna = {"salary_is_predicted": "0", "salary_min": 115000.0, "salary_max": 140000.0}
    assert E.parse_salary("", adzuna) == (115000, 140000, "posting")
    predicted = {"salary_is_predicted": "1", "salary_min": 98000.5, "salary_max": 98000.5}
    assert E.parse_salary("", predicted) == (98000, 98000, "osint_estimate")
    assert E.parse_salary("$120,000 - $150,000", {"salary_is_predicted": "0"}) == (120000, 150000, "posting")
    yearly = {"PositionRemuneration": [{"MinimumRange": "99200.0", "MaximumRange": "128956.0",
                                        "RateIntervalCode": "PA", "Description": "Per Year"}]}
    assert E.parse_salary("", yearly) == (99200, 128956, "posting")
    hourly = {"PositionRemuneration": [{"MinimumRange": "25.0", "MaximumRange": "32.5",
                                        "RateIntervalCode": "PH", "Description": "Per Hour"}]}
    assert E.parse_salary("", hourly) == (None, None, "none")


def test_enrich_notes_a_predicted_salary():
    job = {"title": "SOC Analyst II", "location": "Remote, US", "description": "",
           "raw": {"salary_is_predicted": "1", "salary_min": 100000, "salary_max": 130000}}
    E.enrich(job)
    assert job["salary_source"] == "osint_estimate" and "prediction" in job["salary_note"]
    job["raw"] = {}
    E.enrich(job)
    assert job["salary_note"] is None


def test_employment_structured_search_sources():
    assert E.parse_employment("", {"contract_type": "permanent"}) == "direct"
    # Adzuna tags full-time postings as "contract" too often to fail a role on it
    assert E.parse_employment("Schedule: Full-Time", {"contract_type": "contract"}) == "direct"
    assert E.parse_employment("", {"contract_type": "contract"}) == "unknown"
    assert E.parse_employment("Duration of the Contract: 12 Months") == "contract"
    assert E.parse_employment("Contract length: 6 months, 100% remote") == "contract"
    assert E.parse_employment("", {"PositionOfferingType": [{"Name": "Permanent", "Code": "15317"}]}) == "direct"
    assert E.parse_employment("", {"PositionOfferingType": [{"Name": "Temporary"}]}) == "contract"
    assert E.parse_employment("", {"PositionOfferingType": [{"Name": "Term"}]}) == "unknown"


def test_remote_mode_from_title():
    assert E.parse_remote("", "Austin, Travis County, US", "SOC Analyst II (Remote)") == "remote"
    assert E.parse_remote("", "Chicago, IL", "Threat Hunter - Hybrid") == "hybrid"
    assert E.parse_remote("", "USA - Remote", "Threat Hunter (Hybrid)") == "remote"    # location still wins
    assert E.parse_remote("fully remote", "Chicago, IL", "Threat Hunter") == "remote"


def test_soc_tier_shorthand():
    assert E.parse_seniority("SOC Analyst L1") == "analyst_i"
    assert E.parse_seniority("SOC Analyst (T2)") == "analyst_ii"
    assert E.parse_seniority("SOC L3 Analyst") == "senior"
    assert E.parse_seniority("Security Engineer (L4)") == "analyst_ii"   # ladder levels are level_cap's job
    assert E.has_level_signal("SOC Analyst L2")
    assert not E.has_level_signal("SOC Analyst")
