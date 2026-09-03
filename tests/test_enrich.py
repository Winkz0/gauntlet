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
