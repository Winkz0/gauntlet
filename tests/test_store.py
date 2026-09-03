import json

from pipeline import store
from tests.conftest import make_job


def test_dedup_by_source_id_then_fingerprint(con):
    jid, new = store.upsert_job(con, make_job())
    assert new
    # same source id, new title -> same row, title refreshed
    jid2, new2 = store.upsert_job(con, make_job(title="Incident Response Analyst III", salary_min=1))
    assert (jid2, new2) == (jid, False)
    assert con.execute("SELECT title FROM jobs WHERE id=?", (jid,)).fetchone()[0] == "Incident Response Analyst III"
    # different source, no id, same company/title/location -> fingerprint match
    jid3, new3 = store.upsert_job(con, make_job(source="other", source_job_id=None,
                                                title="Incident Response Analyst III"))
    assert (jid3, new3) == (jid, False)


def test_mark_missing_and_revival(con):
    a, _ = store.upsert_job(con, make_job(source_job_id="a"))
    b, _ = store.upsert_job(con, make_job(source_job_id="b", title="Threat Hunter"))
    assert store.mark_missing(con, "Acme", "test", {a}) == 1
    assert con.execute("SELECT board_status FROM jobs WHERE id=?", (b,)).fetchone()[0] == "gone"
    store.upsert_job(con, make_job(source_job_id="b", title="Threat Hunter"))
    assert con.execute("SELECT board_status FROM jobs WHERE id=?", (b,)).fetchone()[0] == "live"


def test_decisions_and_stages(con):
    jid, _ = store.upsert_job(con, make_job())
    store.save_filter(con, jid, True, [{"rule": "x", "ok": True}])
    assert len(store.digest_candidates(con)) == 1
    store.set_decision(con, jid, "yes")
    assert store.digest_candidates(con) == []
    store.set_stage(con, jid, "queued_for_review", resume_path="output/x/resume.docx")
    store.set_stage(con, jid, "applied")
    row = con.execute("SELECT status, submitted_at, resume_path FROM applications WHERE job_id=?", (jid,)).fetchone()
    assert row["status"] == "applied" and row["submitted_at"] and row["resume_path"].endswith("resume.docx")
    store.clear_decision(con, jid)
    assert len(store.digest_candidates(con)) == 1


def test_invalid_values_rejected(con):
    jid, _ = store.upsert_job(con, make_job())
    import pytest
    with pytest.raises(ValueError):
        store.set_decision(con, jid, "applied")
    with pytest.raises(ValueError):
        store.set_stage(con, jid, "submitted")


def test_migrate_legacy_rows(con):
    jid, _ = store.upsert_job(con, make_job())
    con.execute("INSERT INTO decisions (job_id, decision) VALUES (?, 'applied')", (jid,))
    con.execute("INSERT INTO filter_results (job_id, passed, reasons) VALUES (?, 1, ?)",
                (jid, json.dumps(["remote: ok", "salary: unknown"])))
    done = store.migrate(con)
    assert any("decision" in d for d in done) and any("normalized" in d for d in done)
    assert con.execute("SELECT decision FROM decisions WHERE job_id=?", (jid,)).fetchone()[0] == "yes"
    assert con.execute("SELECT status FROM applications WHERE job_id=?", (jid,)).fetchone()[0] == "applied"
    reasons = json.loads(con.execute("SELECT reasons FROM filter_results WHERE job_id=?", (jid,)).fetchone()[0])
    assert all(isinstance(r, dict) for r in reasons)
    assert store.migrate(con) == []
