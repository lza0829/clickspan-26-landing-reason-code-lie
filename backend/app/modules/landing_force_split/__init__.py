"""着陆事件强制新会话：规则版本快照 + 切分边，同事务关旧开新。

实现见 app.services.landing 与 app.services.ingest；
规则表 landing_rules、切分边表 landing_splits 见 app.seed.SCHEMA。
"""
STATUS = "implemented"
