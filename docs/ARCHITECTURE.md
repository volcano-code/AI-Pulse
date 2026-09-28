# v0.2 架构与故障边界

## 两种执行模式

`local` 保留原版 FastAPI BackgroundTasks 路径，单 API 进程，重启标记未完成任务 interrupted。`durable` 将任务写入数据库，独立 Worker 轮询；API lifespan 不更改 durable 任务。两者不应该在同一 workspace 混合运行。

```text
本地工作台 / Next.js 源码
          │
        FastAPI ── 用户偏好、源、证据、简报、审核
          │
      持久 Run 表  ←── DailySchedule（默认关闭）
          │
    独立 Worker：claim / lease / heartbeat
          │
     采集 → 已完成源检查点 → 简报检查点
          │
   同一数据库事务：任务完成 + 发件箱插入
          │
   Delivery：审核门禁 / file 或显式 SMTP
```

SQLite 队列是本轮选定的本机实现，不是“已经接入 Celery”。尚未加入 Redis、LangGraph 或第三方研究引擎。网络与模型调用不占用长期数据库事务。

## 任务状态与不变量

`queued → running → succeeded / partial / failed / needs_attention`；安全可重试失败或进程失联可 `running → queued`，受次数和退避限制。只允许取消尚未领取的 queued 任务。结果未知的模型调用不会自动重放。

enqueue 使用 workspace 行写锁串行化队列容量与幂等检查。唯一 request_key 对同请求返回同一 Run，对不同 payload 拒绝。领取用条件 SQL 更新和 active_key 唯一约束保留一个 workspace 的执行槽。

每次尝试获得新的随机 lease_token；写入前在同一事务校验 token 和有效租约。旧 Worker 即使晚到也不能覆盖新尝试的数据库状态。租约线程失败不会自称仍持有租约，后续写入由 fence 拒绝。

检查点保存已成功源及已经创建的简报标识。source 的结果和状态保存、brief 写入都经过任务令牌校验。API 重启不会打断独立 Worker。租约/心跳不等于网络硬执行时间限制：系统 DNS 卡住仍是未关闭的外部验收项。

## 每日调度

存储 IANA timezone、HH:MM、enabled、send。Worker 每次扫描按当地日期计算 due instant；同一天唯一 `daily:{schedule_id}:{local_date}`。多个扫描和并发调用不会各建一条日报任务。

DST 重复分钟采用较早瞬间；不存在分钟最多向后搜索 180 分钟，找不到则没有当日有效触发。时区与简报显示时区独立。任务排到下一当地日期会失败，而不是悄悄发送旧日期简报。日后需要历史补发时必须另行设计显式接口。

## 发件箱不变量

任务完成和 outbox 插入同事务提交；发送在事务之后。对 brief + transport + sender + recipient 去重，不因 Worker 重试而重新组装邮件。正文、日期和 Message-ID 冻结。

`blocked_review → pending → sending → file_written / sent / unknown / failed`。可确认的临时拒绝返回 pending 并有限重试；SMTP 提交中断线/超时或者发送租约过期进入 unknown，不能自动再次提交。unknown 只能经过明确人工判定变成 sent 或 pending。

文件传输按 delivery UUID 输出固定 `.eml`，使用临时文件、fsync 和原子替换，重复执行产生同一路径与相同内容。`file_written` 只是本地落盘。进程崩溃重试同样受次数限制。系统断电/文件系统耐久性不在本轮故障验证范围。

SMTP 使用 STARTTLS 或 SSL、默认证书检查、可选账户认证。收件人只能由服务端配置一个。模拟测试覆盖接受、认证失败、4xx/5xx、提交阶段断线、退出失败及人工处理；没有真实服务商验收，因此不承诺 inbox delivery 或 exactly-once。

## 数据与接口

新增 `daily_schedule`、`deliveries`、`worker_heartbeats`；扩展 runs 的 engine、request/request_key、attempts、available_at、lease 和 checkpoint。实际迁移脚本为 `0002_durable_daily_outbox.py`，通过 v0.1 临时数据升级及 Alembic drift 检查。

接口增加 `/automation`、`/automation/schedule`、`/automation/run`、`/deliveries`、`/deliveries/{id}/preview`、`/deliveries/{id}/resolve`、`/briefs/{id}/deliver`、`/runs/{id}/cancel` 等，统一在 `/api/v1` 下。认证沿用单用户 Bearer Token 和本机 Origin 限制；健康接口不需要令牌。完整合同见 [OpenAPI](../contracts/openapi.json)。

## 已验证与未验证

SQLite、受控线程并发、真实子进程崩溃恢复、API/本地工作台 HTTP 桥已执行。PostgreSQL、多机、多租户、真实网络来源、SMTP、LLM、Next.js 原生浏览器端到端未执行或受阻。当前本地词项检索仍不是向量 RAG；引句一致性仍不是事实支持率。

## 设计参考（官方资料）

- SQLAlchemy SQLite 方言与事务：[官方文档](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)
- Python SMTP 异常及接口：[官方文档](https://docs.python.org/3/library/smtplib.html)
- Python 时区与 fold：[官方文档](https://docs.python.org/3/library/zoneinfo.html)
- FastAPI BackgroundTasks 的范围：[官方文档](https://fastapi.tiangolo.com/tutorial/background-tasks/)

这些文档支持设计术语，不构成对本项目实现的外部认证；实现是否正确以源码和本次测试边界为准。
