# AI Pulse · 有来源、有证据的每日 AI 简报

**v0.1.0 / 第一轮基础工程交付。** 这是可以运行的后端、本地验收工作台和 Next.js 前端源码，不是八周完整产品，也不是已上线的自主 Agent 服务。

先运行本地验收界面，可以查看简报、打开原文证据、收藏、调整偏好、检索资料、查看历史版本与任务记录。无需配置模型密钥即可使用原文摘录模式。测试样本和真实采集使用独立数据库，不会自动混入。

![合成测试数据下的本地验收工作台](docs/screenshots/dashboard.png)

> 截图来自 `backend/app/static` 的本地验收页面，显示的是明确标注的合成测试数据。不是 Next.js 构建截图，也不代表真实新闻或真实模型调用。

## 1. 本地启动路径

需要 Python 3.11+。本轮实际测试解释器为 Python 3.13.5；首次安装依赖需要网络。

在解压后的 **ai-pulse 根目录**执行：

```bash
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell 对应：.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
python scripts/run_local.py --demo
```

浏览器打开 `http://127.0.0.1:8000`。脚本会执行 Alembic 迁移，写入六条明确标注的合成样本并生成简报，再启动后端和本地验收界面。合成资料仅用于检查功能，**不要作为资讯传播**。

此时可以直接操作：打开“查看来源与证据”、收藏与取消收藏、切换信息源、保存偏好、检索 `Agent 工作流`、重新生成简报、查看运行记录、导出 Markdown。`REPLAY` 模式中的联网采集按钮被禁用。

停止服务按 `Ctrl+C`。已有进程占用 8000 端口时，启动脚本会拒绝重启或重新播种，避免误改正在运行的任务。

### 换成真实来源

先停止演示进程，再执行：

```bash
python scripts/run_local.py
```

打开工作台，点击 **采集来源**，完成后点击 **生成简报**。默认来源是 Hugging Face Blog、arXiv cs.AI/cs.CL 和 LangGraph GitHub Releases。首次运行需要本机能访问这些服务。

`data/live.db` 与 `data/replay.db` 分开保存；同一数据库不允许切换模式。采集失败不会回填测试样本，不会把旧文章当成今天的内容。默认仅选取最近 72 小时内有明确发布时间的资料，可能生成空简报；可在设置中将窗口改为 1–168 小时。

**本次交付环境真实抓取尝试失败：三个来源均遇到 DNS 错误。** 连接与解析代码已经编写并经过契约测试，但真实来源连通性验收仍需在有外网的环境完成，详见 [验证报告](docs/VALIDATION.md)。

## 2. 运行 Next.js 前端

保持后端在 8000 端口运行，在第二个终端执行：

```bash
cd apps/web
cp .env.example .env.local
npm install
npm run typecheck
npm run build
npm run dev
```

然后打开 `http://127.0.0.1:3000`。运行开发服务不依赖先执行 `build`，这里将构建单独列出，是为了完成当前尚未通过的前端验收。使用生产服务时，在成功构建后执行 `npm run start`，不要同时占用 3000 端口。

前端源码使用 **Next.js 16.3.5、React 19.3.0、TypeScript、Tailwind CSS 4.3.3、TanStack Query**，API 经同源 rewrite 访问后端。包含简报、证据检索、来源、历史、运行记录和偏好设置六个页面视图。

本轮环境无法连接 npm registry，**只完成了 TS/TSX 语法转译检查，没有完成依赖解析、完整类型检查、Next.js 构建或 Next.js 浏览器端到端测试**。未伪造 `package-lock.json`。首次联网安装后应检查依赖、安全公告与兼容性，再提交真实生成的锁文件。

## 3. 当前实际功能

| 能力 | 实现范围 |
|---|---|
| 来源采集 | 固定允许列表，RSS / Atom / GitHub Releases，HTTPS、重定向和读取字节限制 |
| 数据治理 | URL 规范化、同 URL 内容变更快照、精确内容去重；不是跨媒体语义事件聚类 |
| 时间 | 发布时间、更新时间、首次发现时间分开保存；事件发生时间未知时留空 |
| 个性化 | 主题加权、关键词排除、条数、回溯窗口、IANA 时区；不基于点击推断长期兴趣 |
| 简报 | 默认摘录原文；保存不可变证据位置，生成版本与幂等指纹 |
| 模型接入 | OpenAI-compatible Chat Completions 接口、JSON Schema / JSON object、长度与引句检查 |
| 发布门禁 | 模型草稿必须人工批准后才能正式导出；匹配到原文不等于事实已核实 |
| 资料追问 | 有界的本地 BM25 风格词项检索、中英文词项、SSE 引用返回；未调用模型推理 |
| 收藏与历史 | 持久化收藏，历史简报保留原始快照、发布时间及来源元数据 |
| 运行记录 | 本地后台任务、逐阶段记录、来源错误、并发提交限制、中断标记 |
| 接口与测试 | FastAPI OpenAPI、SQLAlchemy、Alembic、pytest、浏览器验收脚本、CI 配置 |

## 4. 数据模式与模型模式是两个不同维度

```text
DATA_MODE=live         使用真实来源；不会自动导入样例
DATA_MODE=replay       使用明确标注的合成测试数据，禁用网络采集

LLM_MODE=extractive    程序提取原文，模型调用数为 0；不是 AI 中文摘要
LLM_MODE=live          实际请求你配置的模型；默认生成待人工复核草稿
```

### 配置真正的模型生成

在 `backend` 目录复制 `.env.example` 为 `.env`，自行填写服务端配置：

```dotenv
LLM_MODE=live
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=your-private-key
LLM_MODEL=your-available-model-id
LLM_RESPONSE_FORMAT=json_schema
```

其中 URL、密钥和模型 ID 都必须替换为你实际可用的配置。这里没有默认假定你拥有某个模型。对只支持 JSON object 的兼容接口可改为 `json_object`；并非所有兼容接口都支持相同的 JSON Schema 子集或 Token 参数，先完成单条资料实测。

用真实模式启动 `python scripts/run_local.py`。`--demo` 始终强制摘录模式，不会用合成样本消耗付费模型额度。

缺少密钥或模型 ID 时，显式的 `live` 配置会启动失败，不会悄悄伪装成模型成功。每条入选资料最多一次模型请求，最多 12 条；无自动模型重试，最多读取 7000 字符，默认输出预算 700 Token。Token 记录只采用供应商返回值；未配置费用表时，金额为 `null`，不能当作免费。失败或超时也可能产生外部计费，应以供应商账单为准。

**真实模型调用尚未验收。** 测试中的模型响应来自 `httpx.MockTransport`，不是付费服务的真实结果。

## 5. 工程目录

```text
ai-pulse/
├── apps/web/                 Next.js / React 工作台源码
├── backend/
│   ├── app/                  API、采集、快照、简报、检索、任务与模型适配
│   │   └── static/           无 Node 构建依赖的本地验收界面
│   ├── migrations/           真实 Alembic 数据库迁移
│   ├── tests/                单元 / 契约 / API 集成测试
│   └── requirements*.txt
├── contracts/openapi.json    从当前后端生成的接口合同
├── scripts/                 启动、浏览器验收、配置与语法检查脚本
├── docs/                    架构、边界、验证记录与实际截图
├── compose.yaml              SQLite + API + Next.js 配置（未运行验收）
├── compose.postgres.yaml     可选 PostgreSQL 覆盖配置（未运行验收）
└── .github/workflows/ci.yml  后端、前端构建及 Next E2E 的 CI 配置
```

## 6. 复跑测试

```bash
python -m pip install -r backend/requirements-dev.txt
cd backend
python -m pytest -q --cov=app --cov-report=term-missing
```

数据库迁移检查应使用新的临时数据库，不要对你的正式数据执行 downgrade：

```bash
DATABASE_URL=sqlite:////tmp/pulse-migration-test.db python -m alembic upgrade head
DATABASE_URL=sqlite:////tmp/pulse-migration-test.db python -m alembic check
```

本地验收页面的浏览器测试需要先启动 `--demo` 服务：

```bash
python -m playwright install chromium
python scripts/browser_acceptance.py
```

脚本仅适用于无访问令牌的本地合成数据验收，会短暂修改偏好、来源开关与收藏，然后恢复。已有自定义收藏时先使用新的演示数据库。本次环境禁止浏览器直接访问网络，因此运行的是明确标记的 `--bridge` 模式：原始页面 JS 通过 Python HTTP 桥连接真实本地 API。它不等同于原生网络浏览器 E2E，更不等同于 Next.js E2E。

Next.js 另提供 `apps/web/tests/workspace.spec.ts` 和 CI 流程，但本次尚未运行。

## 7. 安全和使用边界

这是**单用户、本机开发版本**，不是多租户 SaaS。默认只绑定 `127.0.0.1`。不要移除访问限制后公开暴露服务。设置 `ADMIN_TOKEN` 后，全部业务 API 需要 Bearer Token；浏览器通过“访问令牌”入口输入。令牌仅保存在当前标签页，模型密钥只保存在后端。

本地允许的浏览器 Origin 是 localhost / 127.0.0.1 的 3000 和 8000 端口；公开域名部署需要专门配置认证、Origin、TLS、反向代理和数据隔离，不能照搬本地设置。原文采用文本渲染；来源内容不会被作为 HTML 执行。采集来源不是用户可提交的任意 URL。

DNS 校验和主机允许列表是基础防护，不是消除 DNS 重绑定风险的完整网络隔离。生产环境还需要出站访问策略或抓取代理。模型没有可调用的外部写工具，但这并不意味着已经完成全面提示注入红队测试。

本地任务使用 FastAPI `BackgroundTasks` 和数据库状态，不是 Celery 持久任务队列。**仅使用一个 API worker。** 重启会将未完成任务标记为 interrupted，需重新提交；不支持恢复某次模型调用的中间状态。CLI 初始化、迁移、演示播种不能与运行中的 API 并发操作。

## 8. 尚未实现的部分

不把下面这些列为当前成果：定时日报调度和自动邮件/飞书推送、跨媒体语义事件聚类、跨日事实增量分析、LangGraph 自主研究、多 Agent 协作、Celery/Redis、向量 RAG 与 Reranker、长期记忆学习、MCP/Skills、Langfuse/OpenTelemetry、多用户认证和权限隔离。

Docker 和 PostgreSQL 提供了配置文件，但当前环境没有 Docker，未完成容器和 PostgreSQL 实测。代码不意味着这些部署已验收。当前没有远程 GitHub 提交、PR 或线上部署；源码交付于本压缩包。

下一轮应首先关闭真实来源、真实模型、Next.js 构建三个验收缺口，再引入持久任务与自主研究，不以继续叠加框架替代当前闭环的真实验收。

更多细节见 [架构说明](docs/ARCHITECTURE.md)、[验证报告](docs/VALIDATION.md)、[后续开发任务](docs/NEXT_TASKS.md)。
