# v0.3 当前架构，不是目标架构

```text
本地原生 JS 工作台 / 待构建的 Next.js 源码
                    │ 受控 REST
                 FastAPI（1 个进程）
                    │
       SQLAlchemy + SQLite（单 workspace）
          ├─ sources / articles / snapshots / briefs
          ├─ runs / worker heartbeats / outbox
          ├─ reading_states：已读快照，不写回历史资料
          └─ investigations：状态、引用、工具日志、停止原因
                    │
             独立数据库队列 Worker
          ├─ 每日调度 / 阶段检查点 / 幂等
          ├─ 可强制终止的来源抓取子进程
          └─ .eml 或用户明确配置的 SMTP

研究路径（独立于发布/投递）
问题 → 搜索已保存快照 → 检查原文引用 → 返回证据/待审核草稿
           ↑ 最多两种只读工具，工具/模型次数与时间上限 ↓
                 持久审计与历史结果
```

默认 `extractive` 为确定性本地词法检索；可选 `live` 为模型驱动工具选择，当前只有模拟契约验证。新研究请求同步执行，完成前 HTTP 连接可能等待最多配置截止；不是流式 LangGraph DAG 或可恢复付费任务。

API 启动把旧 running 调查标记 needs_attention，避免盲目计费重放。仅一个 API 进程适用；另起 API 会打断正在运行的状态解释。普通每日任务仍使用已有 Worker 租约、阶段检查点和发件箱。

新增路由见 `contracts/openapi.json`：`/api/v1/updates`、`/articles/{id}/changes`、`/articles/{id}/read`、`/investigations`、`/investigations/{id}`。旧 `/ask`/`/ask/stream` 保留兼容。

阅读差异仅基于原文，最大每侧 30,000 Unicode 字符、80 个变化片段；截断明确可见。未实现跨源事件聚类、增量事实模型或自动学习偏好。

不具备：多租户、向量检索、独立事实核验、MCP、Skills、LangGraph、Redis/Celery、DeepSeek Harness、生产代理出站隔离。
