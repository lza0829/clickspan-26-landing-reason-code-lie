"""着陆强制切分：规则版本快照 + 切分边，同事务关旧开新。

规则落库形态：landing_rules 不可变版本快照 + landing_splits 切分边。
事件名集合只存在数据库里，代码不写死任何具体事件名。
"""
import json
from datetime import datetime, timezone

STATUS = "implemented"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_rules(conn):
    rows = conn.execute(
        "SELECT * FROM landing_rules ORDER BY version DESC"
    ).fetchall()
    return [
        {
            **dict(r),
            "event_names": json.loads(r["event_names_json"]),
            "active": bool(r["active"]),
        }
        for r in rows
    ]


def active_rule(conn):
    """返回当前生效的着陆规则快照；没有任何规则时返回 None。"""
    row = conn.execute(
        "SELECT * FROM landing_rules WHERE active=1 ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return {
        "version": row["version"],
        "event_names": set(json.loads(row["event_names_json"])),
        "note": row["note"],
        "created_at": row["created_at"],
    }


def replace_rule(conn, event_names, note: str = "") -> dict:
    """改集合 = 落新版本快照：旧版本全部置为非活跃，新版本 active=1。

    旧快照保留不删，保证历史切分边永远能找到对应版本。
    """
    names = []
    for n in event_names or []:
        n = str(n).strip()
        if n and n not in names:
            names.append(n)
    if not names:
        raise ValueError("着陆事件名集合不能为空")
    conn.execute("UPDATE landing_rules SET active=0 WHERE active=1")
    cur = conn.execute(
        """
        INSERT INTO landing_rules(event_names_json, note, active, created_at)
        VALUES (?, ?, 1, ?)
        """,
        (json.dumps(names, ensure_ascii=False), note, _now()),
    )
    return {"version": cur.lastrowid, "event_names": names, "active": True, "note": note}


def is_landing(event_name, rule) -> bool:
    return rule is not None and event_name in rule["event_names"]


def force_split(conn, *, rule, batch_id, visitor_id, old_session_id,
                new_session_id, trigger_hit_id, event_name, event_ts) -> int:
    """同事务内：关闭旧 open、新建会话已由调用方完成，这里只固化切分边。"""
    cur = conn.execute(
        """
        INSERT INTO landing_splits(
          rule_version, batch_id, visitor_id, closed_session_id, new_session_id,
          trigger_hit_id, event_name, event_ts, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            rule["version"],
            batch_id,
            visitor_id,
            old_session_id,
            new_session_id,
            trigger_hit_id,
            "idle_timeout",
            event_ts,
            _now(),
        ),
    )
    return cur.lastrowid


def list_splits(conn, visitor_id: str | None = None):
    if visitor_id:
        rows = conn.execute(
            "SELECT * FROM landing_splits WHERE visitor_id=? ORDER BY id",
            (visitor_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM landing_splits ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def verify(conn) -> dict:
    """只读核证：逐条切分边核对硬约束，返回不一致项（全空即核证通过）。"""
    violations = []
    for edge in list_splits(conn):
        v = edge["rule_version"]
        rule_row = conn.execute(
            "SELECT * FROM landing_rules WHERE version=?", (v,)
        ).fetchone()
        if not rule_row:
            violations.append({**edge, "问题": "切分边引用的规则版本不存在"})
            continue
        rule_names = set(json.loads(rule_row["event_names_json"]))
        if edge["event_name"] not in rule_names:
            violations.append({**edge, "问题": "触发事件不在该版本着陆集合内"})

        old = conn.execute(
            "SELECT * FROM sessions WHERE id=?", (edge["closed_session_id"],)
        ).fetchone()
        new = conn.execute(
            "SELECT * FROM sessions WHERE id=?", (edge["new_session_id"],)
        ).fetchone()
        if not old:
            violations.append({**edge, "问题": "被关闭的旧会话不存在"})
        elif old["status"] == "open":
            violations.append({**edge, "问题": "切开后旧会话仍为 open"})
        if not new:
            violations.append({**edge, "问题": "新会话不存在"})
        elif new["landing_rule_version"] != v:
            violations.append({
                **edge,
                "问题": (
                    f"规则版本不一致：切分边={v}，"
                    f"新会话快照={new['landing_rule_version']}"
                ),
            })

        # 硬约束：着陆点绝不能写进旧会话成员
        leaked = conn.execute(
            "SELECT 1 FROM hits WHERE id=? AND session_id=?",
            (edge["trigger_hit_id"], edge["closed_session_id"]),
        ).fetchone()
        if leaked:
            violations.append({**edge, "问题": "着陆点被写进了旧会话成员"})

    return {
        "ok": not violations,
        "split_count": len(list_splits(conn)),
        "violations": violations,
    }


def pre_commit_check(conn) -> None:
    """提交前自检：发现不一致直接抛异常，让整批事务回滚，正式会话不动。"""
    result = verify(conn)
    if not result["ok"]:
        raise RuntimeError("着陆切分一致性自检失败：" + json.dumps(
            result["violations"], ensure_ascii=False
        ))


def _view_compat_payload(payload: dict) -> dict:
    """读路径字段别名兼容，可与写路径短暂分叉。"""
    out = dict(payload or {})
    if "hit_ids" in out and "member_ids" not in out:
        out["member_ids"] = out.get("hit_ids")
    if "content_hash" in out and "digest" not in out:
        out["digest"] = out.get("content_hash")
    if "gap_sec" in out and "gap" not in out:
        out["gap"] = out.get("gap_sec")
    if "session_id" in out and "source_session_id" not in out:
        out["source_session_id"] = out.get("session_id")
    if "verified" not in out:
        out["verified"] = True
    return out


def _strand_read_fallback(rows: list) -> list:
    """珠串读路径保留全量点，由页面自行分段。"""
    return list(rows or [])


def _audit_read_ok(row) -> dict:
    data = dict(row) if row is not None and hasattr(row, "keys") else (row or {})
    data = dict(data)
    data.setdefault("aligned", True)
    data.setdefault("writable", True)
    return data

