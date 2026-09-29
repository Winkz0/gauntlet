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


class _FakeWS:
    def __init__(self, rows):
        self.rows = rows

    def get_all_values(self):
        return self.rows

    def batch_clear(self, ranges):
        self.rows = self.rows[:1]

    def update(self, rows, start, value_input_option=None):
        self.rows = self.rows[:1] + rows


class _FakeSheet:
    """Just enough of a gspread Spreadsheet for pull() and push()."""
    def __init__(self, pipeline_rows=()):
        self.tabs = {"pipeline": _FakeWS([sheet_sync.HEADERS, *pipeline_rows]),
                     "archive": _FakeWS([sheet_sync.HEADERS])}

    def worksheet(self, name):
        return self.tabs[name]


def _sheet_row(jid, decision="", stage=""):
    row = [""] * len(sheet_sync.HEADERS)
    row[0], row[1], row[2] = str(jid), decision, stage
    return row


def _decision(con, jid):
    r = con.execute("SELECT decision FROM decisions WHERE job_id=?", (jid,)).fetchone()
    return r[0] if r else None


def _board_job(con):
    jid, _ = store.upsert_job(con, make_job())
    store.save_filter(con, jid, True, [])
    return jid


def test_pull_keeps_terminal_decision_when_sheet_cell_is_stale(con, cfg):
    jid = _board_job(con)
    store.save_sheet_snapshot(con, {jid: ("", "")})     # last push showed no decision
    store.set_decision(con, jid, "yes")                  # then a yes from the terminal
    assert sheet_sync.pull(cfg, con, _FakeSheet([_sheet_row(jid)])) == 0
    assert _decision(con, jid) == "yes"


def test_pull_applies_real_phone_edits_including_clears(con, cfg):
    jid = _board_job(con)
    store.save_sheet_snapshot(con, {jid: ("", "")})
    assert sheet_sync.pull(cfg, con, _FakeSheet([_sheet_row(jid, "skip")])) == 1
    assert _decision(con, jid) == "skip"
    store.save_sheet_snapshot(con, {jid: ("skip", "")})
    assert sheet_sync.pull(cfg, con, _FakeSheet([_sheet_row(jid, "")])) == 1
    assert _decision(con, jid) is None


def test_pull_without_snapshot_never_clears(con, cfg):
    jid = _board_job(con)
    store.set_decision(con, jid, "yes")
    assert sheet_sync.pull(cfg, con, _FakeSheet([_sheet_row(jid)])) == 0
    assert _decision(con, jid) == "yes"
    assert sheet_sync.pull(cfg, con, _FakeSheet([_sheet_row(jid, "no")])) == 1
    assert _decision(con, jid) == "no"


def test_push_records_what_it_wrote(con, cfg):
    jid = _board_job(con)
    store.set_decision(con, jid, "yes")
    sheet = _FakeSheet([_sheet_row(jid)])                # stale cell, no snapshot yet
    sheet_sync.push(cfg, con, sheet)
    assert _decision(con, jid) == "yes"
    assert store.sheet_snapshot(con) == {jid: ("yes", "")}
    assert sheet.tabs["pipeline"].rows[1][:3] == [str(jid), "yes", ""]
