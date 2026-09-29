from pipeline.source import REGISTRY_PATH, load_registry


def test_local_registry_appends_companies(tmp_path):
    local = tmp_path / "registry.local.yaml"
    local.write_text("companies+:\n  - name: ExampleCorp\n    board: greenhouse\n    slug: examplecorp\n")
    base = load_registry(local_path=None)
    layered = load_registry(REGISTRY_PATH, local_path=local)
    assert [c["name"] for c in layered["companies"]] == [c["name"] for c in base["companies"]] + ["ExampleCorp"]


def test_registry_without_local_file(tmp_path):
    assert load_registry(local_path=tmp_path / "missing.yaml") == load_registry(local_path=None)
