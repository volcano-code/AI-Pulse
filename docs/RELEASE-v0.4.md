# AI Pulse v0.4.0-dev · Event Layer + Integration Diagnostics

## 本轮已执行通过

- v0.3.0 后端基线复跑：262 passed。
- 新增安全的来源失败诊断码；定向网络/隔离抓取测试 27 passed。
- 新增稳定事件层：`events`、`event_articles`、`event_versions`，保留文章来源和快照，不覆盖证据。
- 事件层定向/API/既有 pipeline 回归通过；最终把全部测试文件分成两个互斥批次执行，**137 + 132 = 269 passed，0 failed**。
- v0.3 → v0.4 SQLite 迁移验收：旧表逐值哈希保持一致；0004 可 downgrade 到 0003 后再升级。
- Durable Worker 强杀恢复：4 项检查通过，只产生一份本地投递文件。
- Python compile 与 TS/TSX 语法检查通过。

## 真实集成门禁

### 真实来源 canary：已执行失败 / 环境阻塞

本环境实际执行三个来源，没有用合成数据替代：

- arXiv：`dns_error`
- Hugging Face Blog：`dns_error`
- LangGraph GitHub Releases：`deadline_exceeded`
- live 数据库保存真实文章：0

v0.4 现在会返回机器可读的安全失败码，但不会暴露 URL 中的敏感片段、响应正文、请求头或密钥。

### Next.js：环境阻塞

`npm ping` 实际返回 `EAI_AGAIN registry.npmjs.org`；因此无法生成可信 lockfile，也没有完成 `npm install`、`typecheck`、`next build` 或原生 Playwright E2E。已有 TS/TSX 语法检查不能替代这些门禁。

### PostgreSQL / Docker：环境阻塞

当前执行环境没有 Docker。0004 已在 SQLite 上完成 Alembic 往返验收，但 PostgreSQL、Compose、pgvector 和并发语义仍未实际执行。

### 真实 LLM / SMTP：未执行

本轮模型调用与 SMTP 提交均为 0。保留 v0.3 的 MockTransport/契约测试与人工审核门禁；没有把模拟调用称作真实供应商验收。

## 事件层边界

当前事件匹配是确定性的标题词项 + 字符序列相似度，并非 Embedding 语义聚类。事件 membership 一旦建立不会自动漂移；文章当前快照集合变化会创建新的 EventVersion。下一阶段应在冻结标注集上评估误合并/漏合并，再决定是否引入 Embedding/Reranker。

## 事件匹配冻结 sanity set

新增 12 对中英文合成标题的回归集；当前阈值 0.52 上得到 precision/recall/F1 = 1.0。**这只用于防止确定性匹配器代码回归，不代表真实新闻数据上的生产指标。**真实标注集仍是下一门禁。
