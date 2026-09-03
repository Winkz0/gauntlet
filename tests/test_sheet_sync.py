from pipeline import sheet_sync, store
from tests.conftest import make_job


def _age(con, jid, days):
    con.execute("UPDATE jobs SET first_seen=datetime('now', ?) WHERE id=?", (f"-{days} days", jid))


def test_classify_retention(con, cfg):
    cfg["sheet"]["archive_after_days"] = 8
    fresh, _ = store.upsert_job(con, make_job(source_job_id="f"))
    old, _ = store.upsert_job(con, make_job(source_job_id="o", title="Threat Hunter"))
    said_no, _ = store.upsert_job(con, make_job(source_job_id="n", title="SOC Analyst"))
    applied, _ = store.upsert_job(con, make_job(source_job_id="a", title="DFIR Analyst"))
    gone, _ = store.upsert_job(con, make_job(source_job_id="g", title="Detection Engineer"))
    for j in (fresh, old, said_no, applied, gone):
        store.save_filter(con, j, True, [])
    _age(con, old, 9)
    store.set_decision(con, said_no, "no")
    store.set_decision(con, applied, "yes"); store.set_stage(con, applied, "applied")
    _age(con, applied, 30)
    con.execute("UPDATE jobs SET board_status='gone', gone_since=datetime('now','-5 days') WHERE id=?", (gone,))

    tabs = sheet_sync.build_rows(con, cfg)
    ids = {k: {int(r[0]) for r in v} for k, v in tabs.items()}
    assert ids["pipeline"] == {fresh, applied}
    assert ids["archive"] == {old, said_no, gone}


def test_headers_match_row_width(con, cfg):
    jid, _ = store.upsert_job(con, make_job())
    store.save_filter(con, jid, True, [{"rule": "salary", "ok": True, "tag": "strong"}])
    rows = sheet_sync.build_rows(con, cfg)["pipeline"]
    assert len(rows) == 1 and len(rows[0]) == len(sheet_sync.HEADERS)
    rec = dict(zip(sheet_sync.HEADERS, rows[0]))
    assert rec["tags"] == "strong" and rec["days_open"] == "0"
