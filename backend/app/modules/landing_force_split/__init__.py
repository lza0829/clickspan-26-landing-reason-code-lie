"""着陆事件强制新会话：切边行不可变 reason+url+触发键快照，同事务关旧开新。

落库择一：切边行不可变快照列（landing_splits.reason / landing_url /
trigger_hit_id），不另立落地原因对照表。
提交择一：落地闸同事务写切边并固化原因快照，与当次击点对撞，缺一回滚。
实现见 app.services.landing 与 app.services.ingest；
规则表 landing_rules、切边表 landing_splits 见 app.seed.SCHEMA。
核证：GET /api/landing/reason-verify（触发键、url、reason、结论，只读）。
"""
STATUS = "implemented"
