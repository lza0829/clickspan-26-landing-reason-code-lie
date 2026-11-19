import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import seed  # noqa: E402


_FIXTURES = {
    "landing_hits.json": {
        "batch_id": "B-landing-test",
        "hits": [
            {"id": "L1", "visitor_id": "V-9001", "event_ts": "2026-11-18T09:00:00Z",
             "page_path": "/home", "event_name": "view", "visitor_label": "甲"},
            {"id": "L2", "visitor_id": "V-9001", "event_ts": "2026-11-18T09:00:30Z",
             "page_path": "/item/9", "event_name": "click", "visitor_label": "甲"},
            {"id": "L3", "visitor_id": "V-9001", "event_ts": "2026-11-18T09:01:00Z",
             "page_path": "/lp/a", "event_name": "landing", "visitor_label": "甲"},
            {"id": "L4", "visitor_id": "V-9001", "event_ts": "2026-11-18T09:01:20Z",
             "page_path": "/lp/a/offer", "event_name": "view", "visitor_label": "甲"},
            {"id": "L5", "visitor_id": "V-9001", "event_ts": "2026-11-18T09:01:40Z",
             "page_path": "/lp/a", "event_name": "landing", "visitor_label": "甲"},
            {"id": "L6", "visitor_id": "V-9001", "event_ts": "2026-11-18T09:02:00Z",
             "page_path": "/lp/a/form", "event_name": "view", "visitor_label": "甲"},
        ],
    },
    "browse_hits.json": {
        "batch_id": "B-browse-test",
        "hits": [
            {"id": "N1", "visitor_id": "V-9002", "event_ts": "2026-11-18T10:00:00Z",
             "page_path": "/home", "event_name": "view", "visitor_label": "乙"},
            {"id": "N2", "visitor_id": "V-9002", "event_ts": "2026-11-18T10:00:40Z",
             "page_path": "/search", "event_name": "view", "visitor_label": "乙"},
            {"id": "N3", "visitor_id": "V-9002", "event_ts": "2026-11-18T10:01:20Z",
             "page_path": "/item/3", "event_name": "click", "visitor_label": "乙"},
            {"id": "N4", "visitor_id": "V-9002", "event_ts": "2026-11-18T10:02:00Z",
             "page_path": "/item/3", "event_name": "view", "visitor_label": "乙"},
        ],
    },
    "idle_gap.json": {
        "batch_id": "B-idle-test",
        "hits": [
            {"id": "G1", "visitor_id": "V-9003", "event_ts": "2026-11-18T11:00:00Z",
             "page_path": "/home", "event_name": "view", "visitor_label": "丙"},
            {"id": "G2", "visitor_id": "V-9003", "event_ts": "2026-11-18T11:40:00Z",
             "page_path": "/home", "event_name": "view", "visitor_label": "丙"},
        ],
    },
}


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    fx_dir = tmp_path / "fixtures"
    data_dir.mkdir()
    fx_dir.mkdir()
    monkeypatch.setattr("app.db.DATA_DIR", data_dir)
    monkeypatch.setattr("app.db.DB_PATH", data_dir / "clickspan.db")
    monkeypatch.setattr("app.db.FIXTURES_DIR", fx_dir)
    monkeypatch.setattr("app.seed.FIXTURES_DIR", fx_dir)
    monkeypatch.setattr("app.services.ingest.FIXTURES_DIR", fx_dir)
    for name, payload in _FIXTURES.items():
        (fx_dir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    seed.init_db()
    yield fx_dir


@pytest.fixture()
def client(fresh_db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
