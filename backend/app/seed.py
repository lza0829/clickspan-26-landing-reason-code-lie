import json

from app.db import FIXTURES_DIR, connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS visitors (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  visitor_id TEXT NOT NULL REFERENCES visitors(id),
  started_at TEXT NOT NULL,
  ended_at TEXT,
  hit_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS hits (
  id TEXT PRIMARY KEY,
  visitor_id TEXT NOT NULL REFERENCES visitors(id),
  session_id TEXT REFERENCES sessions(id),
  event_ts TEXT NOT NULL,
  page_path TEXT NOT NULL,
  event_name TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'primary',
  UNIQUE(visitor_id, event_ts, page_path, event_name)
);

CREATE TABLE IF NOT EXISTS quarantine (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  visitor_id TEXT,
  reason TEXT NOT NULL,
  member_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_batches (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  report_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS session_proofs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  visitor_id TEXT NOT NULL,
  hit_ids_json TEXT NOT NULL,
  hit_count INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  created_at TEXT NOT NULL
);

-- 着陆规则表：每次改集合都新增不可变版本，旧版本只翻转 active，不允许改写事件名
CREATE TABLE IF NOT EXISTS landing_rules (
  version INTEGER PRIMARY KEY AUTOINCREMENT,
  event_names_json TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

-- 着陆切分边：记录关旧开新的边界及其规则版本（与 landing_rules 快照互证）
-- reason / landing_url / trigger_hit_id 为不可变快照列：落地闸同事务固化，无改写路径
CREATE TABLE IF NOT EXISTS landing_splits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_version INTEGER NOT NULL,
  batch_id TEXT,
  visitor_id TEXT NOT NULL,
  closed_session_id TEXT NOT NULL,
  new_session_id TEXT NOT NULL,
  trigger_hit_id TEXT NOT NULL UNIQUE,
  event_name TEXT NOT NULL,
  event_ts TEXT NOT NULL,
  reason TEXT NOT NULL,
  landing_url TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    _migrate(conn)
    row = conn.execute("SELECT value FROM settings WHERE key='idle_timeout_sec'").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('idle_timeout_sec', ?)",
            ("1800",),
        )
    rule = conn.execute("SELECT version FROM landing_rules WHERE active=1").fetchone()
    if not rule:
        conn.execute(
            """
            INSERT INTO landing_rules(event_names_json, note, active, created_at)
            VALUES (?, ?, 1, datetime('now'))
            """,
            (
                json.dumps(["landing", "landing_page"], ensure_ascii=False),
                "初始着陆事件集合（可在切窗页改）",
            ),
        )
    n = conn.execute("SELECT COUNT(*) AS c FROM visitors").fetchone()["c"]
    if n == 0:
        _seed_fixtures(conn)
    conn.commit()
    conn.close()


def _migrate(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "landing_rule_version" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN landing_rule_version INTEGER")
    split_cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(landing_splits)").fetchall()
    }
    if "reason" not in split_cols:
        conn.execute(
            "ALTER TABLE landing_splits ADD COLUMN reason TEXT NOT NULL DEFAULT 'landing_force'"
        )
    if "landing_url" not in split_cols:
        conn.execute(
            "ALTER TABLE landing_splits ADD COLUMN landing_url TEXT NOT NULL DEFAULT ''"
        )
    # 历史快照修复：落地 URL / 事件名一律回填为触发击点本体值（修复原因码撒谎期旧行）
    conn.execute(
        """
        UPDATE landing_splits SET landing_url = (
          SELECT page_path FROM hits WHERE hits.id = landing_splits.trigger_hit_id
        )
        WHERE landing_url = '' AND EXISTS (
          SELECT 1 FROM hits WHERE hits.id = landing_splits.trigger_hit_id
        )
        """
    )
    conn.execute(
        """
        UPDATE landing_splits SET event_name = (
          SELECT event_name FROM hits WHERE hits.id = landing_splits.trigger_hit_id
        )
        WHERE EXISTS (
          SELECT 1 FROM hits
          WHERE hits.id = landing_splits.trigger_hit_id
            AND hits.event_name != landing_splits.event_name
        )
        """
    )


def _seed_fixtures(conn):
    fixture = FIXTURES_DIR / "seed_hits.json"
    if not fixture.exists():
        return
    data = json.loads(fixture.read_text(encoding="utf-8"))
    for v in data.get("visitors", []):
        conn.execute(
            "INSERT INTO visitors(id, label, created_at) VALUES (?,?,?)",
            (v["id"], v["label"], v["created_at"]),
        )
    # 基座：按访客整段落进一个 open 会话（超时切分留给功能题空桩）
    for visitor_id, group in _group_hits(data.get("hits", [])).items():
        group = sorted(group, key=lambda h: (h["event_ts"], h["id"]))
        sid = f"S-{visitor_id}-1"
        conn.execute(
            "INSERT INTO sessions(id, visitor_id, started_at, ended_at, hit_count, status) VALUES (?,?,?,?,?,?)",
            (
                sid,
                visitor_id,
                group[0]["event_ts"],
                group[-1]["event_ts"],
                len(group),
                "open",
            ),
        )
        hit_ids = []
        for h in group:
            conn.execute(
                """
                INSERT INTO hits(id, visitor_id, session_id, event_ts, page_path, event_name, channel)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    h["id"],
                    visitor_id,
                    sid,
                    h["event_ts"],
                    h["page_path"],
                    h["event_name"],
                    h.get("channel", "primary"),
                ),
            )
            hit_ids.append(h["id"])
        conn.execute(
            """
            INSERT INTO session_proofs(session_id, visitor_id, hit_ids_json, hit_count, started_at, ended_at, created_at)
            VALUES (?,?,?,?,?,?,datetime('now'))
            """,
            (
                sid,
                visitor_id,
                json.dumps(hit_ids, ensure_ascii=False),
                len(hit_ids),
                group[0]["event_ts"],
                group[-1]["event_ts"],
            ),
        )


def _group_hits(hits):
    out = {}
    for h in hits:
        out.setdefault(h["visitor_id"], []).append(h)
    return out


def idle_timeout_sec(conn) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key='idle_timeout_sec'").fetchone()
    return int(row["value"]) if row else 1800
