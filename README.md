# AI Pulse · 每日 AI 简报与引用式研究工作台

单用户、本机优先的 AI 资讯工作台：采集 → 不可变原文快照 → 引用式简报 → 人工审核 → 文件或显式 SMTP 投递；支持增量阅读和基于已保存证据的受限研究。

> 当前代码基线包含截至 v0.8 的增量功能，包版本字符串仍保留 `0.4.0-dev`，不能单靠版本号判断能力。本文已修正旧 README 的状态漂移；历史 RELEASE/VALIDATION 文档保留当时结果，不代表当前版本全部状态。最新本轮验证见 [阶段 0/1 验收](docs/BASELINE-AND-STATUS.md)。

## 已实现与边界

| 功能 | 当前实现 | 不等于 |
|---|---|---|
| 来源采集 | Hugging Face Blog、arXiv API、LangGraph Releases；截止时间、健康状态、不可变快照 | 任意网页自主抓取或来源长期可用 |
| 简报 | 偏好、时间过滤、去重、来源均衡、事件版本、人工审核、历史及 Markdown 导出 | 摘要事实支持率已达标 |
| 增量阅读 | 已读绑定快照，原文变化和引用位置 | 语义事实核验 |
| 研究 | 有界只读工具循环、引用证据、执行记录、历史重开 | 无限联网 Agent、LangGraph 或多 Agent |
| Hybrid RAG | 词项检索 + pgvector 精确余弦检索 + RRF，按证据作用域过滤；明确降级 | SQLite 向量服务、ANN 索引或真实供应商已验收 |
| 后台运行 | SQL Worker、租约/检查点、每日时区调度、事务性发件箱 | 多租户、多机生产部署 |
| 调用审计 | 模型/Embedding 预留、成功复用、未知结果阻断、人工单次恢复 | 服务商 exactly-once 计费或金额预算 |
| 前端 | Next.js + React + TypeScript；lockfile、typecheck/build、原生浏览器 CI 已有 | 用户本机 Docker 已验收 |

`/api/v1/status` 的 `capabilities.vector_search=true` 表示代码支持；新增 `retrieval` 对象报告当前配置与本地前置条件，**不发起模型请求**。`local_prerequisites_ready` 不证明指定问题有可用证据、供应商健康或答案质量；实际问题以返回的 requested/effective mode 和 degradation reason 为准。`provider_health=not_probed` 不得显示为在线。

真实付费 LLM、Embedding、SMTP、金额预算、长期运行及中文事实支持质量仍需独立验收。合成资料、fixture provider、文件投递不能充当真实闭环证据。

## 1. 无密钥本地演示

需要 Python 3.11+。从仓库根目录运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python scripts/run_local.py --demo --durable
```

Windows 用 `.venv\Scripts\python.exe` 替换 `.venv/bin/python`。访问 `http://127.0.0.1:8000` 的本地原生 JS 页面，Ctrl+C 停止 API/Worker。启动器执行 `alembic upgrade head`；不要手工 stamp 或降级正式库。演示使用独立 replay 数据库、明确 SYNTHETIC 资料、摘录模式与 `.eml` 文件，不发送真实邮件或调用付费模型。不要继承真实 Embedding/SMTP 配置进入演示。

Next.js 前端另开终端（CI 使用 Node 22）：

```bash
cd apps/web
cp .env.example .env.local  # 仅首次；不要覆盖自己的配置
npm ci
npm run typecheck
NODE_ENV=production npm run build
npm run start
```

访问 `http://127.0.0.1:3000`。Windows 可分别设置 `NODE_ENV=production` 再执行 build。所有密钥留在服务端，禁止放入 `NEXT_PUBLIC_*`。

## 2. 真实资料与服务

停止演示后运行不带 `--demo` 的启动器。默认 live 与 replay 数据库隔离；真实来源失败时显示错误，不填入合成资料。

首次复制 `backend/.env.example` 为 `backend/.env`，按需配置。默认 `LLM_MODE=extractive`、`RETRIEVAL_MODE=lexical`、`DELIVERY_TRANSPORT=file`，不需要付费服务。使用模型时设置 `LLM_MODE=live`、可信 `LLM_BASE_URL`、密钥及模型名，先做显式小额 canary；供应商兼容性不能只凭“OpenAI-compatible”推定。

混合检索还需要 PostgreSQL/pgvector、迁移、按同一 provider/model/dim 建立的 corpus vectors；配置 `RETRIEVAL_MODE=hybrid` 和完整 Embedding 元数据。`EMBEDDING_QUERY_ALLOW_PAID` 默认为 false，语料回填授权不等于在线问题调用授权。详见 [调用审计](docs/PROVIDER-CALL-AUDIT.md)、[查询审计与恢复](docs/QUERY-AUDIT-AND-RECOVERY.md)。

SMTP 必须显式配置固定收件人及 TLS；文件 `file_written` 不是邮件发送，SMTP 接受也不是收件箱送达。未知结果禁止自动重发。每日调度默认关闭，进程或电脑停止时不会执行；模型草稿仍需人工审核。

## 3. Docker Compose 部署

已有数据的部署升级前，先阅读[集成升级与回退边界](docs/INTEGRATION-20261118.md)：停止API/worker并验证备份后再迁移；Docker不提供启动器的自动SQLite备份。

参见 [部署与故障排查](docs/DEPLOYMENT.md)。不要把 `-p` 直接传给 `docker`，也不要依赖丢失的 shell 函数。

```bash
python scripts/create_compose_env.py  # 仅首次，拒绝覆盖已有 .env
python scripts/compose.py --postgres -- config --quiet
python scripts/compose.py --postgres -- up -d --build --wait
python scripts/compose.py --postgres -- ps
```

此流程会启动服务，但不会自行触发采集、付费 canary 或发信。Docker/Compose 与本机容量需实际验收。不要把展开的 `compose config`、`.env` 或服务日志中的个人资料公开上传。

## 4. 测试与验收

```bash
.venv/bin/python -m pip install -r backend/requirements-dev.txt
cd backend
../.venv/bin/python -m pytest -q
```

PostgreSQL-only 测试默认跳过，跳过不是通过。CI 包含 backend、postgres、frontend、browser-e2e 四项；[原生浏览器说明](docs/BROWSER-AND-SOURCE-BALANCE.md)包含运行条件。公开真实来源探测与普通离线测试分开，参见 [真实来源验收](docs/LIVE-EVIDENCE-ACCEPTANCE.md)。

真实供应商、SMTP、生产环境不得由测试自动调用。升级前停止 API/Worker，备份配置与数据库；破坏性迁移只运行在一次性测试数据库。

## 5. 安全边界与后续工作

仅面向单用户本机，默认 loopback；可配置 `ADMIN_TOKEN`，不要直接暴露公网。固定 URL 和 DNS 检查不替代生产出站隔离。使用一个 API 进程；没有新增多用户身份系统、LangGraph、Redis、Celery 或 MCP。引用位置正确不证明概括正确。

[完整后续阶段与门槛](docs/NEXT_TASKS.md) · [模型运行审计](docs/RUNTIME-MODEL-AUDIT.md) · [查询恢复](docs/QUERY-AUDIT-AND-RECOVERY.md) · [历史变更](CHANGELOG.md)
