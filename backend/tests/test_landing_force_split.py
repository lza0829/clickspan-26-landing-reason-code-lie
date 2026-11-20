import json

import pytest

from app.db import connect
from app.services import ingest, landing


def _sessions_of(vid):
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE visitor_id=? ORDER BY started_at", (vid,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _hits_of(vid):
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM hits WHERE visitor_id=? ORDER BY event_ts", (vid,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# 1. 着陆夹具：间隙仅几十秒（远未超 idle），两个 landing 必须各切一次 → 3 段
def test_landing_forces_split_ignoring_idle_gap(client):
    res = client.post("/api/ingest/run", json={"fixture": "landing_hits.json"})
    assert res.status_code == 200
    assert res.json()["ok"] is True
    report = res.json()["report"]
    assert report["landing_splits"] == 2
    assert report["idle_splits"] == 0

    sessions = _sessions_of("V-9001")
    assert len(sessions) == 3
    # 硬约束：切开后旧会话不得仍为 open
    assert [s["status"] for s in sessions] == ["closed", "closed", "open"]

    hits = {h["id"]: h for h in _hits_of("V-9001")}
    s1, s2, s3 = [s["id"] for s in sessions]
    # 硬约束：着陆点绝不能写进旧会话成员
    assert [hits["L1"]["session_id"], hits["L2"]["session_id"]] == [s1, s1]
    assert hits["L3"]["session_id"] == s2
    assert hits["L4"]["session_id"] == s2
    assert hits["L5"]["session_id"] == s3
    assert hits["L6"]["session_id"] == s3

    # 新会话挂上切分所用的规则版本
    rule_version = sessions[1]["landing_rule_version"]
    assert rule_version is not None
    assert sessions[2]["landing_rule_version"] == rule_version


# 2. 普通浏览夹具：同样小间隙、无着陆事件 → 全程一段，无切分边
def test_browse_fixture_stays_one_session(client):
    res = client.post("/api/ingest/run", json={"fixture": "browse_hits.json"})
    report = res.json()["report"]
    assert report["landing_splits"] == 0
    assert report["idle_splits"] == 0

    sessions = _sessions_of("V-9002")
    assert len(sessions) == 1
    assert sessions[0]["status"] == "open"
    assert sessions[0]["hit_count"] == 4

    splits = client.get("/api/landing/splits").json()
    assert splits == []


# 3. 非着陆事件继续走空闲超时：40 分钟间隙（idle=1800s）切开
def test_non_landing_uses_idle_timeout(client):
    res = client.post("/api/ingest/run", json={"fixture": "idle_gap.json"})
    report = res.json()["report"]
    assert report["idle_splits"] == 1
    assert report["landing_splits"] == 0
    sessions = _sessions_of("V-9003")
    assert len(sessions) == 2
    assert sessions[0]["status"] == "closed"
    assert sessions[1]["status"] == "open"


# 4. 切分边与规则版本快照一致，核证只读接口通过
def test_split_edges_and_verify(client):
    client.post("/api/ingest/run", json={"fixture": "landing_hits.json"})
    edges = client.get("/api/landing/splits").json()
    assert len(edges) == 2
    versions = {e["rule_version"] for e in edges}
    assert len(versions) == 1

    verify = client.get("/api/landing/verify").json()
    assert verify["ok"] is True
    assert verify["split_count"] == 2
    assert verify["violations"] == []

    # 切分边记录的触发点就是新会话首点
    for e in edges:
        hit = next(h for h in _hits_of("V-9001") if h["id"] == e["trigger_hit_id"])
        assert hit["session_id"] == e["new_session_id"]
        assert hit["event_name"] == "landing"


# 5. 着陆集合可改：新版本快照生效，旧版本保留且置为非活跃
def test_rule_set_change_creates_versioned_snapshot(client):
    before = client.get("/api/landing/rules").json()
    assert any(r["active"] for r in before)
    old_version = next(r["version"] for r in before if r["active"])

    res = client.put("/api/landing/rules", json={
        "event_names": ["campaign_view"], "note": "改集合测试"
    })
    assert res.status_code == 200
    new_version = res.json()["version"]
    assert new_version > old_version

    rules = client.get("/api/landing/rules").json()
    active = [r for r in rules if r["active"]]
    assert len(active) == 1
    assert active[0]["event_names"] == ["campaign_view"]
    old = next(r for r in rules if r["version"] == old_version)
    assert old["active"] is False

    # 旧集合里的 landing 不再触发切分
    client.post("/api/ingest/run", json={"fixture": "landing_hits.json"})
    assert _sessions_of("V-9001").__len__() == 1
    assert client.get("/api/landing/splits").json() == []

    # 空集合拒绝
    bad = client.put("/api/landing/rules", json={"event_names": []})
    assert bad.status_code == 400


# 6. 失败闭：写入阶段异常 → 整批回滚，正式会话不动
def test_failure_rolls_back_whole_batch(client, monkeypatch):
    real_force = landing.force_split
    calls = {"n": 0}

    def boom(conn, **kw):
        calls["n"] += 1
        if calls["n"] == 2:  # 第二个着陆点切分时炸
            raise RuntimeError("注入故障")
        return real_force(conn, **kw)

    monkeypatch.setattr(ingest.landing_svc, "force_split", boom)
    res = client.post("/api/ingest/run", json={"fixture": "landing_hits.json"})
    body = res.json()
    assert body["ok"] is False
    assert "注入故障" in body["error"]

    # 回滚后无任何会话/击点/切分边落地
    conn = connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM sessions WHERE visitor_id='V-9001'"
        ).fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) c FROM hits WHERE visitor_id='V-9001'"
        ).fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM landing_splits").fetchone()["c"] == 0
    finally:
        conn.close()

    # 故障排除后重跑同一夹具应完整成功
    monkeypatch.setattr(ingest.landing_svc, "force_split", real_force)
    ok = client.post("/api/ingest/run", json={"fixture": "landing_hits.json"})
    assert ok.json()["ok"] is True
    assert len(_sessions_of("V-9001")) == 3
    assert client.get("/api/landing/verify").json()["ok"] is True


# 7. 提交前自检：版本不一致不得标成功（模拟被污染的切分边）
def test_precommit_check_blocks_version_mismatch(client, monkeypatch):
    real_force = landing.force_split

    def tampered(conn, **kw):
        edge_id = real_force(conn, **kw)
        conn.execute(
            "UPDATE landing_splits SET rule_version=rule_version+999 WHERE id=?",
            (edge_id,),
        )
        return edge_id

    monkeypatch.setattr(ingest.landing_svc, "force_split", tampered)
    res = client.post("/api/ingest/run", json={"fixture": "landing_hits.json"})
    assert res.json()["ok"] is False

    conn = connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM sessions WHERE visitor_id='V-9001'"
        ).fetchone()["c"] == 0
    finally:
        conn.close()


# 8. 幂等重放：重复收流同一批不产生重复切分
def test_replay_is_idempotent(client):
    first = client.post("/api/ingest/run", json={"fixture": "landing_hits.json"}).json()
    assert first["report"]["written"] == 6
    second = client.post("/api/ingest/run", json={"fixture": "landing_hits.json"}).json()
    assert second["report"]["written"] == 0
    assert len(client.get("/api/landing/splits").json()) == 2
    assert client.get("/api/landing/verify").json()["ok"] is True


def test_landing_event_names_not_hardcoded_in_branch_logic():
    """代码分支只认数据库规则集：把 landing 从集合拿掉后 is_landing 为假。"""
    rule = {"version": 1, "event_names": {"other_event"}}
    assert landing.is_landing("landing", rule) is False
    assert landing.is_landing("other_event", rule) is True
    assert landing.is_landing("landing", None) is False


# 9. 落地原因核证（只读接口）：触发键、url、reason、结论 逐边可核
def test_reason_verify_tracks_reason_trigger_key_and_url(client):
    client.post("/api/ingest/run", json={"fixture": "landing_hits.json"})
    res = client.get("/api/landing/reason-verify")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["edge_count"] == 2
    # 原因对照可核：落地类码与超时类码都在对照字典里
    assert body["reason_codes"]["landing_force"] == "着陆事件强制切窗"
    assert body["reason_codes"]["idle_timeout"] == "空闲超时切窗"
    assert body["landing_reason_codes"] == ["landing_force"]

    hits = {h["id"]: h for h in _hits_of("V-9001")}
    assert {e["trigger_hit_id"] for e in body["edges"]} == {"L3", "L5"}
    for edge in body["edges"]:
        assert edge["conclusion"] == "ok"
        assert edge["problems"] == []
        # 硬约束：落地切开 reason 必须是落地类码，不得为超时类
        assert edge["reason"] == "landing_force"
        assert edge["reason"] != "idle_timeout"
        # 硬约束：触发键命中当次真实着陆击点
        hit = hits[edge["trigger_hit_id"]]
        assert hit["event_name"] == "landing"
        assert hit["session_id"] == edge["new_session_id"]
        # 硬约束：URL 快照非空且与触发击点页面一致
        assert edge["landing_url"]
        assert edge["landing_url"] == hit["page_path"] == "/lp/a"


# 10. 超时切开对照：空闲切窗不得误标落地
def test_idle_split_not_mislabeled_as_landing(client):
    res = client.post("/api/ingest/run", json={"fixture": "idle_gap.json"})
    assert res.json()["report"]["idle_splits"] == 1
    assert res.json()["report"]["landing_splits"] == 0
    # 超时切开不落任何着陆切边，原因核证零边通过
    assert client.get("/api/landing/splits").json() == []
    rv = client.get("/api/landing/reason-verify").json()
    assert rv["ok"] is True
    assert rv["edge_count"] == 0
    # 被空闲切开的会话详情也不得挂落地切边
    sessions = _sessions_of("V-9003")
    assert len(sessions) == 2
    detail = client.get(f"/api/sessions/{sessions[0]['id']}").json()
    assert detail["landing_split_out"] is None


# 11. 会话详情读路径不撒谎：触发键、reason、url 如实返回
def test_session_detail_reports_true_reason_and_trigger_key(client):
    client.post("/api/ingest/run", json={"fixture": "landing_hits.json"})
    sessions = _sessions_of("V-9001")

    out = client.get(f"/api/sessions/{sessions[0]['id']}").json()["landing_split_out"]
    assert out["trigger_hit_id"] == "L3"
    assert out["event_name"] == "landing"
    assert out["reason"] == "landing_force"
    assert out["landing_url"] == "/lp/a"

    inc = client.get(f"/api/sessions/{sessions[1]['id']}").json()["landing_split_in"]
    assert inc["trigger_hit_id"] == "L3"
    assert inc["event_name"] == "landing"
    assert inc["reason"] == "landing_force"
    assert inc["landing_url"] == "/lp/a"


# 12. 落地切开而 reason 被改成超时类 → 提交前自检拦下，整批回滚
def test_precommit_blocks_timeout_reason_on_landing_edge(client, monkeypatch):
    real_force = landing.force_split

    def tampered(conn, **kw):
        edge_id = real_force(conn, **kw)
        conn.execute(
            "UPDATE landing_splits SET reason='idle_timeout' WHERE id=?", (edge_id,)
        )
        return edge_id

    monkeypatch.setattr(ingest.landing_svc, "force_split", tampered)
    res = client.post("/api/ingest/run", json={"fixture": "landing_hits.json"})
    assert res.json()["ok"] is False

    conn = connect()
    try:
        assert conn.execute("SELECT COUNT(*) c FROM landing_splits").fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) c FROM sessions WHERE visitor_id='V-9001'"
        ).fetchone()["c"] == 0
    finally:
        conn.close()


# 13. URL 快照张冠李戴 → 提交前自检拦下，整批回滚
def test_precommit_blocks_foreign_landing_url(client, monkeypatch):
    real_force = landing.force_split

    def tampered(conn, **kw):
        edge_id = real_force(conn, **kw)
        conn.execute(
            "UPDATE landing_splits SET landing_url='/elsewhere' WHERE id=?",
            (edge_id,),
        )
        return edge_id

    monkeypatch.setattr(ingest.landing_svc, "force_split", tampered)
    res = client.post("/api/ingest/run", json={"fixture": "landing_hits.json"})
    assert res.json()["ok"] is False

    conn = connect()
    try:
        assert conn.execute("SELECT COUNT(*) c FROM landing_splits").fetchone()["c"] == 0
    finally:
        conn.close()


# 14. 落地闸对撞：快照与当次击点本体不符即拒写（缺一回滚的写路径半边）
def test_force_split_rejects_snapshot_diverging_from_hit(client):
    client.post("/api/ingest/run", json={"fixture": "landing_hits.json"})
    conn = connect()
    try:
        rule = landing.active_rule(conn)
        base = dict(
            rule=rule, batch_id="B-x", visitor_id="V-9001",
            old_session_id="s-old", new_session_id="s-new",
            trigger_hit_id="L3", event_name="landing",
            event_ts="2026-11-18T09:01:00Z", landing_url="/lp/a",
        )
        # 事件名撒谎（写成超时类）
        with pytest.raises(ValueError):
            landing.force_split(conn, **{**base, "event_name": "idle_timeout"})
        # URL 快照为空
        with pytest.raises(ValueError):
            landing.force_split(conn, **{**base, "landing_url": ""})
        # URL 快照张冠李戴
        with pytest.raises(ValueError):
            landing.force_split(conn, **{**base, "landing_url": "/other"})
        # 触发键指向非本访客击点
        with pytest.raises(ValueError):
            landing.force_split(conn, **{**base, "trigger_hit_id": "G1"})
    finally:
        conn.rollback()
        conn.close()
