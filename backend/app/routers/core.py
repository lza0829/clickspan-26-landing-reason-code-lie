from fastapi import APIRouter, HTTPException

from app.db import connect
from app.seed import idle_timeout_sec
from app.services import ingest as ingest_svc
from app.services import landing as landing_svc
from app.services import sessions as sess_svc

router = APIRouter(prefix="/api")


@router.get("/overview")
def overview():
    conn = connect()
    try:
        rule = landing_svc.active_rule(conn)
        return {
            "visitors": conn.execute("SELECT COUNT(*) AS c FROM visitors").fetchone()["c"],
            "sessions": conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"],
            "hits_primary": conn.execute(
                "SELECT COUNT(*) AS c FROM hits WHERE channel='primary'"
            ).fetchone()["c"],
            "quarantine": conn.execute("SELECT COUNT(*) AS c FROM quarantine").fetchone()["c"],
            "idle_timeout_sec": idle_timeout_sec(conn),
            "landing_rule_version": rule["version"] if rule else None,
            "landing_event_names": sorted(rule["event_names"]) if rule else [],
            "landing_splits": conn.execute(
                "SELECT COUNT(*) AS c FROM landing_splits"
            ).fetchone()["c"],
        }
    finally:
        conn.close()


@router.get("/visitors")
def list_visitors():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, label, created_at FROM visitors ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/sessions")
def list_sessions(visitor_id: str | None = None):
    conn = connect()
    try:
        return sess_svc.list_sessions(conn, visitor_id)
    finally:
        conn.close()


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    conn = connect()
    try:
        detail = sess_svc.session_detail(conn, session_id)
        if not detail:
            raise HTTPException(404, "会话不存在")
        return detail
    finally:
        conn.close()


@router.get("/quarantine")
def list_quarantine():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, visitor_id, reason, member_json, created_at FROM quarantine ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/ingest/run")
def run_ingest(body: dict | None = None):
    fixture = (body or {}).get("fixture", "ingest_batch.json")
    return ingest_svc.run_batch(fixture)


@router.get("/session-proofs")
def session_proofs(session_id: str | None = None):
    conn = connect()
    try:
        if session_id:
            rows = conn.execute(
                "SELECT * FROM session_proofs WHERE session_id=? ORDER BY id DESC",
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM session_proofs ORDER BY id DESC LIMIT 50"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/settings")
def get_settings():
    conn = connect()
    try:
        return {"idle_timeout_sec": idle_timeout_sec(conn)}
    finally:
        conn.close()


@router.put("/settings/idle-timeout")
def put_idle_timeout(body: dict):
    sec = int(body.get("idle_timeout_sec", 1800))
    if sec < 60:
        raise HTTPException(400, "空闲超时至少 60 秒")
    conn = connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('idle_timeout_sec', ?)",
            (str(sec),),
        )
        conn.commit()
        return {"idle_timeout_sec": sec}
    finally:
        conn.close()


# ---- 着陆规则配置（版本快照，可改集合） ----


@router.get("/landing/rules")
def get_landing_rules():
    conn = connect()
    try:
        return landing_svc.list_rules(conn)
    finally:
        conn.close()


@router.put("/landing/rules")
def put_landing_rule(body: dict):
    names = body.get("event_names")
    if not isinstance(names, list) or not names:
        raise HTTPException(400, "event_names 必须是非空数组")
    conn = connect()
    try:
        try:
            rule = landing_svc.replace_rule(conn, names, body.get("note", ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        conn.commit()
        return rule
    finally:
        conn.close()


@router.get("/landing/splits")
def get_landing_splits(visitor_id: str | None = None):
    """只读：着陆切分边。"""
    conn = connect()
    try:
        return landing_svc.list_splits(conn, visitor_id)
    finally:
        conn.close()


@router.get("/landing/verify")
def landing_verify():
    """只读：着陆切开核证。"""
    conn = connect()
    try:
        return landing_svc.verify(conn)
    finally:
        conn.close()


@router.get("/landing/reason-verify")
def landing_reason_verify(visitor_id: str | None = None):
    """只读：落地原因核证——逐条切边给出 触发键 / url / reason / 结论。"""
    conn = connect()
    try:
        return landing_svc.verify_reasons(conn, visitor_id)
    finally:
        conn.close()


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

