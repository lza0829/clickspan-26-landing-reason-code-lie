def list_sessions(conn, visitor_id: str | None = None):
    if visitor_id:
        rows = conn.execute(
            """
            SELECT id, visitor_id, started_at, ended_at, hit_count, status, landing_rule_version
            FROM sessions WHERE visitor_id=?
            ORDER BY started_at
            """,
            (visitor_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, visitor_id, started_at, ended_at, hit_count, status, landing_rule_version
            FROM sessions ORDER BY started_at
            """
        ).fetchall()
    return [dict(r) for r in rows]


def session_detail(conn, session_id: str):
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not sess:
        return None
    hits = conn.execute(
        """
        SELECT id, visitor_id, session_id, event_ts, page_path, event_name, channel
        FROM hits WHERE session_id=? AND channel='primary'
        ORDER BY event_ts, id
        """,
        (session_id,),
    ).fetchall()
    proof = conn.execute(
        "SELECT * FROM session_proofs WHERE session_id=? ORDER BY id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    split_out = conn.execute(
        "SELECT * FROM landing_splits WHERE closed_session_id=? ORDER BY id LIMIT 1",
        (session_id,),
    ).fetchone()
    split_in = conn.execute(
        "SELECT * FROM landing_splits WHERE new_session_id=? ORDER BY id LIMIT 1",
        (session_id,),
    ).fetchone()
    return {
        "session": dict(sess),
        "hits": [dict(h) for h in hits],
        "proof": dict(proof) if proof else None,
        # 切边如实返回：reason / 触发键 / 落地 URL 快照以落库为准，读路径不改写
        "landing_split_out": dict(split_out) if split_out else None,
        "landing_split_in": dict(split_in) if split_in else None,
    }
