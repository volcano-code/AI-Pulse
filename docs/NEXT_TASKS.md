# v0.4 后续硬门禁

1. 在可联网环境完成 `npm install` 并提交真实 `package-lock.json`，随后改 CI 为 `npm ci`，跑 `typecheck + next build + Playwright`。
2. 在可联网环境重新运行三个真实来源 canary；逐源核对发布时间、URL、更新、重复与真实快照。
3. 在 Docker/PostgreSQL 环境执行 0001→0004 迁移、重启、并发 Worker 与持久卷验收；通过后再考虑 pgvector。
4. 构建事件聚类冻结标注集，至少覆盖同事件多来源、版本升级、同名不同产品、中文/英文标题；报告 pairwise precision/recall。
5. 事件层质量稳定后实现 Hybrid RAG：Postgres FTS/BM25-like + Embedding + RRF，保留词项检索基线并做 Recall@20 对照。
6. 用户显式配置真实模型后，仅做单条 canary：结构化输出、usage、超时未知状态、审核门禁；不自动重试可能计费的未知请求。
7. LangGraph、MCP/Skills、多租户继续后置；只有在真实闭环和检索评测通过后引入。
