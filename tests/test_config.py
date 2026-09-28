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
