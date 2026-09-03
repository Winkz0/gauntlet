import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import store  # noqa: E402
from pipeline.config import load_cfg  # noqa: E402


@pytest.fixture
def cfg():
    return load_cfg()


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "t.db"
    store.init_db(str(db))
    c = store.connect(str(db))
    yield c
    c.close()


def make_job(**over):
    base = {
        "source": "test", "source_job_id": "1", "company": "Acme",
        "title": "Incident Response Analyst II", "location": "Remote, US",
        "url": "https://example.com/1", "description": "Full-time. $120,000 - $150,000.",
        "raw": {},
    }
    base.update(over)
    return base
