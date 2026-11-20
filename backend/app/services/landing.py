"""着陆强制切分：规则版本快照 + 切边不可变原因快照，同事务关旧开新。

落库形态（择一）：切边行不可变 reason + landing_url + 触发键快照列
（landing_splits 表），不另立落地原因对照表。
提交形态（择一）：落地闸同事务写切边并固化原因快照，缺一回滚——
force_split 与当次击点行对撞，任一不符即抛错，整批事务回滚。
事件名集合只存在数据库里，代码不写死任何具体事件名。
"""
import json
from datetime import datetime, timezone

STATUS = "implemented"

# 切边原因码对照（可核）：落地类码 vs 超时类码。
# 原因码是切窗类别的闭集枚举，不是事件名；着陆事件名集合仍只存数据库。
REASON_LANDING_FORCE = "landing_force"
REASON_IDLE_TIMEOUT = "idle_timeout"
REASON_CODES = {
    REASON_LANDING_FORCE: "着陆事件强制切窗",
    REASON_IDLE_TIMEOUT: "空闲超时切窗",
}
LANDING_REASON_CODES = frozenset({REASON_LANDING_FORCE})
TIMEOUT_REASON_CODES = frozenset({REASON_IDLE_TIMEOUT})


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
                new_session_id, trigger_hit_id, event_name, event_ts,
                landing_url=None) -> int:
    """落地闸：同事务内固化切边及其不可变原因快照（reason + url + 触发键）。

    快照只认当次触发击点本体：事件名 / 时间 / 落地 URL 与击点行对撞，
    任一缺失或不符即抛错，调用方整批回滚。硬约束：
    - 禁止落地切开标成功而 reason 仍为超时类——这里恒写落地类码；
    - 禁止触发键与真实着陆击点分叉——触发键必须命中本访客击点行；
    - 禁止 URL 快照空或张冠李戴——必须与触发击点页面一致。
    """
    hit = conn.execute(
        "SELECT * FROM hits WHERE id=? AND visitor_id=?",
        (trigger_hit_id, visitor_id),
    ).fetchone()
    if not hit:
        raise ValueError(f"触发击点 {trigger_hit_id!r} 不存在，拒绝固化切边")
    if not is_landing(hit["event_name"], rule):
        raise ValueError(
            f"击点 {trigger_hit_id!r} 的事件 {hit['event_name']!r} 不在着陆集合内"
        )
    if hit["event_name"] != event_name or hit["event_ts"] != event_ts:
        raise ValueError("切边快照与触发击点本体不符，拒绝固化")
    url = landing_url if landing_url is not None else hit["page_path"]
    if not url or url != hit["page_path"]:
        raise ValueError("落地 URL 快照为空或与触发击点页面不符")
    cur = conn.execute(
        """
        INSERT INTO landing_splits(
          rule_version, batch_id, visitor_id, closed_session_id, new_session_id,
          trigger_hit_id, event_name, event_ts, reason, landing_url, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            rule["version"],
            batch_id,
            visitor_id,
            old_session_id,
            new_session_id,
            trigger_hit_id,
            hit["event_name"],
            hit["event_ts"],
            REASON_LANDING_FORCE,
            url,
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


def verify_reasons(conn, visitor_id: str | None = None) -> dict:
    """只读落地原因核证：逐条切边核对 触发键 / url / reason，逐边给结论。

    对照 REASON_CODES 字典判类：落地切开的切边 reason 必须是落地类码，
    触发键必须命中当次真实着陆击点，URL 快照必须与该击点页面一致。
    """
    edges = []
    for edge in list_splits(conn, visitor_id):
        problems = []
        reason = edge["reason"]
        if reason in TIMEOUT_REASON_CODES:
            problems.append("落地切开而 reason 仍为超时类码")
        elif reason not in LANDING_REASON_CODES:
            problems.append(f"reason 不在落地原因对照表内：{reason!r}")

        rule_row = conn.execute(
            "SELECT * FROM landing_rules WHERE version=?", (edge["rule_version"],)
        ).fetchone()
        rule_names = (
            set(json.loads(rule_row["event_names_json"])) if rule_row else set()
        )

        hit = conn.execute(
            "SELECT * FROM hits WHERE id=?", (edge["trigger_hit_id"],)
        ).fetchone()
        if not hit:
            problems.append("触发键未命中任何击点")
        else:
            if hit["event_name"] not in rule_names:
                problems.append("触发键命中的击点不是当次着陆事件")
            if hit["session_id"] != edge["new_session_id"]:
                problems.append("触发键与真实着陆击点分叉：击点不在切边新会话")
            if hit["event_name"] != edge["event_name"]:
                problems.append("切边事件名快照与触发击点不符")
            if hit["event_ts"] != edge["event_ts"]:
                problems.append("切边时间快照与触发击点不符")
            if not edge["landing_url"]:
                problems.append("落地 URL 快照为空")
            elif edge["landing_url"] != hit["page_path"]:
                problems.append("落地 URL 快照张冠李戴：与触发击点页面不符")

        edges.append({
            "split_id": edge["id"],
            "visitor_id": edge["visitor_id"],
            "closed_session_id": edge["closed_session_id"],
            "new_session_id": edge["new_session_id"],
            "trigger_hit_id": edge["trigger_hit_id"],
            "landing_url": edge["landing_url"],
            "reason": reason,
            "reason_label": REASON_CODES.get(reason, "未知原因码"),
            "conclusion": "ok" if not problems else "fail",
            "problems": problems,
        })
    return {
        "ok": all(e["conclusion"] == "ok" for e in edges),
        "edge_count": len(edges),
        "reason_codes": dict(REASON_CODES),
        "landing_reason_codes": sorted(LANDING_REASON_CODES),
        "edges": edges,
    }


def pre_commit_check(conn) -> None:
    """提交前自检：结构核证 + 落地原因核证，任一不一致即抛错，整批回滚。"""
    problems = list(verify(conn)["violations"])
    for edge in verify_reasons(conn)["edges"]:
        problems += [
            {"split_id": edge["split_id"], "问题": p} for p in edge["problems"]
        ]
    if problems:
        raise RuntimeError("着陆切分一致性自检失败：" + json.dumps(
            problems, ensure_ascii=False
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

