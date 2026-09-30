from pipeline.config import CONFIG_PATH, load_cfg, merge_local


def test_merge_local_rules():
    base = {"a": [1, 2], "b": {"x": 1, "y": 2}, "c": "keep"}
    merge_local(base, {"a+": [3], "b": {"y": 20, "z": 30}, "c": "new"})
    assert base == {"a": [1, 2, 3], "b": {"x": 1, "y": 20, "z": 30}, "c": "new"}
    merge_local(base, {"a": [9], "d+": ["fresh"]})
    assert base["a"] == [9] and base["d"] == ["fresh"]


def test_load_cfg_applies_local_file(tmp_path):
    local = tmp_path / "config.local.yaml"
    local.write_text("role_keywords_block+: [zzz-term]\nfilters:\n  salary_floor: 1\n")
    base = load_cfg(local_path=None)
    layered = load_cfg(CONFIG_PATH, local_path=local)
    assert layered["role_keywords_block"] == base["role_keywords_block"] + ["zzz-term"]
    assert layered["filters"]["salary_floor"] == 1
    assert layered["filters"]["remote_types_ok"] == base["filters"]["remote_types_ok"]


def test_load_cfg_without_local_file(tmp_path):
    assert load_cfg(local_path=tmp_path / "missing.yaml") == load_cfg(local_path=None)


def test_search_keys_come_from_secrets(monkeypatch):
    import pipeline.config as C
    monkeypatch.setattr(C, "load_secrets", lambda: {
        "ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "key",
        "USAJOBS_API_KEY": "ukey", "USAJOBS_EMAIL": "me@example.com"})
    cfg = C.load_cfg(local_path=None)
    assert cfg["search"]["adzuna"]["app_id"] == "id" and cfg["search"]["adzuna"]["app_key"] == "key"
    assert cfg["search"]["adzuna"]["results_per_page"] == 50                 # committed settings kept
    assert cfg["search"]["usajobs"]["api_key"] == "ukey"
    assert cfg["search"]["usajobs"]["email"] == "me@example.com"


def test_role_families_are_well_formed():
    cfg = load_cfg(local_path=None)
    levels = {"analyst_i", "analyst_ii", "senior", "lead", "principal"}
    for name, fam in cfg["role_families"].items():
        assert fam.get("variants") and fam.get("queries"), name
        assert fam.get("min_level") in (None, *levels), name


def test_local_overlay_merges_into_a_family(tmp_path):
    local = tmp_path / "config.local.yaml"
    local.write_text("role_families:\n  soc_analyst:\n    min_level: analyst_ii\n    queries+: [soc tier 2]\n")
    base = load_cfg(local_path=None)["role_families"]["soc_analyst"]
    fam = load_cfg(CONFIG_PATH, local_path=local)["role_families"]["soc_analyst"]
    assert fam["min_level"] == "analyst_ii"
    assert fam["variants"] == base["variants"]
    assert fam["queries"] == base["queries"] + ["soc tier 2"]
