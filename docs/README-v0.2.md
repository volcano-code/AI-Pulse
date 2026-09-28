# AI Pulse · 每日 AI 简报工作台

**v0.2.0 / 本地开发版本：质量修复 + 独立 Worker + 每日调度 + 事务性发件箱。**

这不是完整研究 Agent，也没有完成生产上线验收。后端、本地工作台、SQLite 迁移、独立进程恢复已在本轮环境执行验证；真实资讯抓取、真实模型、真实 SMTP、Next.js 完整构建、Docker/PostgreSQL 仍未验收通过。

**默认不向外部发送任何邮件。** `file` 传输只保存 `.eml` 预览，界面显示 `file_written`；不会把它写成“已送达邮箱”。测试资料与真实资料分库存储。

[质量复检报告](docs/QUALITY_REPORT.md) · [验证记录](docs/VALIDATION.md) · [架构与状态机](docs/ARCHITECTURE.md) · [版本变更](CHANGELOG.md) · [剩余验收门禁](docs/NEXT_TASKS.md)

![合成测试资料下的 v0.2 每日调度与发件箱](docs/screenshots/v0.2/automation.png)

> 图片来自真实本地后端 + 独立 Worker + SQLite 的验收工作台，内容为显式合成样本。浏览器使用测试专用 Python HTTP 桥，不是原生浏览器网络验收，也不是 Next.js 页面。

## 1. 首次启动：直接验收每日流程

需要 Python 3.11+；实际验证为 Python 3.13.5。首次安装依赖需要可用的包索引网络。

在解压得到的 `ai-pulse` 根目录执行：

```bash
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
python scripts/run_local.py --demo --durable
```

浏览器打开 `http://127.0.0.1:8000`。

启动脚本依次执行 SQLite 迁移、导入明确标注的六条合成样本、启动独立 Worker 和一个 API 进程。按 `Ctrl+C` 会停止这两个子进程。没有 Node 依赖也可以验收本地工作台。

进入 **“每日自动化”**，确认 Worker 在线。勾选“生成后加入发件箱”，点击“立即运行一次每日流程”；在“运行记录”查看任务，再刷新调度页，查看 `file_written` 记录并打开 `.eml`。

每日定时任务**默认关闭**。填写 IANA 时区（如 `Asia/Shanghai`）和当地时间，明确启用并保存后才会定时运行。调度时区与“偏好设置”里的简报显示时区独立，需要一致时分别设置。电脑休眠、进程关闭或 Worker 离线时不能准点执行；恢复后仅补当前当地日期，不追发多天历史内容。

`--demo` 强制 `DATA_MODE=replay`、`LLM_MODE=extractive`、`DELIVERY_TRANSPORT=file`。不会把合成资料发送给付费模型或 SMTP。

## 2. 升级已有 v0.1 数据

先停止旧 API、Worker 和 CLI。保留旧版 `data/`、自己的 `.env` 和源代码副本，不直接覆盖唯一备份。

```bash
python scripts/run_local.py --durable --database-url sqlite:////absolute/path/to/old/live.db
# 原库为 replay 模式时必须同时带 --demo；不允许把 replay 库作为 live 打开。
```

默认路径是项目根目录的 `data/live.db` 和 `data/replay.db`。启动脚本在迁移旧 SQLite 文件前会使用 SQLite backup API 创建带时间戳的 `.bak`，然后执行 `0001 → 0002`。这是停机迁移，不是热升级。临时库升级测试保留了原有文章、快照、简报和任务的所有旧列值，见 [迁移证据](docs/validation-v0.2/migration-acceptance.json)。

如果原数据库由 `create_all` 创建但没有 Alembic 版本记录，启动可能因已有表而拒绝迁移；**不要直接 stamp head 或删表**，先确认旧表结构并保留备份。不要对正式库执行报告里的 downgrade 测试。

## 3. 真实来源模式

停止演示服务后执行：

```bash
python scripts/run_local.py --durable
```

默认三个来源为 Hugging Face Blog、arXiv API 和 LangGraph GitHub Releases。来源适配器只读取配置的 feed / API，不抓取任意网页全文。点击“采集来源”或运行每日流程会尝试联网；默认仍是原文摘录，不会调用付费模型。

**本轮真实抓取再次受阻，受控命令在 25 秒上限内未完成；单独 npm registry 探测确认 DNS 失败。** 未生成真实来源验收成功记录，未用合成内容填充 live 数据库。网络/系统 DNS 延迟可能超过 HTTP 超时，尚未实现抓取进程级总截止时间；这是外部集成门禁之一。

默认选最近 72 小时内有可靠发布时间且在截止时已采集的资料。可能得到空简报；空简报不会进入发件箱。部分来源失败会显示部分完成和来源健康信息；全部来源无有效结果会退避重试，耗尽预算后失败。

## 4. 真实模型和真实邮件：独立、明确配置

复制 `backend/.env.example` 为 `backend/.env`，只在自己的机器上填写密钥；不要提交到版本库或粘贴进公开日志。

真实模型配置：

```dotenv
LLM_MODE=live
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=your-private-key
LLM_MODEL=your-available-model-id
LLM_RESPONSE_FORMAT=json_schema
```

端点、模型名和密钥必须是真实可用的配置。兼容接口可能只支持 `json_object` 或不同 Token 参数，需先验证一条资料。当前每条入选资料最多一次请求，最多 12 条；尚无基于真实价格表的金额预算。`cost_usd=null` 不是免费。

模型草稿为 `needs_review`，不会自动发送。人工批准后才放行对应 `blocked_review` 发件箱记录。原文引句匹配仅证明定位一致，不等于每条陈述已经被独立事实核验。

真实邮件配置：

```dotenv
DELIVERY_TRANSPORT=smtp
SMTP_HOST=your-trusted-smtp.example
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=your-account
SMTP_PASSWORD=your-private-password
MAIL_FROM=sender@your-domain.example
MAIL_TO=one-explicit-recipient@your-domain.example
```

配置示例必须替换为自己的服务信息；465 端口通常配合 `SMTP_SECURITY=ssl`。TLS 校验不可关闭。当前只支持一名服务端配置的收件人，不支持任意动态收件人或群发。

SMTP 的 `sent` 表示服务器接受了提交，不保证进入收件箱。发送阶段断线/超时或发送进程失联后记录为 `unknown`，**禁止自动重发**。需根据服务商日志明确选择“确认已发送”或“确认未发送，允许重试”；错误的人为判定仍可能产生重复邮件。真实 SMTP 本轮没有提交，所有 SMTP 验证均为模拟服务器对象的契约测试。

## 5. 本轮实现范围

| 能力 | 实现与边界 |
|---|---|
| 数据质量修复 | 来源不可被其他 feed 覆盖；全无效条目不算成功；无内容变化不新增简报；截止后快照不回流 |
| 持久任务 | 数据库队列、幂等键、最多 20 个活动/排队任务、原子领取、租约、令牌隔离、阶段检查点 |
| 独立 Worker | 与 API 分进程；API 重启不重置 durable 任务；失联后在预算内恢复 |
| 每日调度 | 时区、当地时间、当天去重；夏令时重复时间取第一次，缺失时间向后找有效分钟 |
| 发件箱 | 与任务完成同事务写入；冻结正文/Message-ID；本地 `.eml` 或可选 SMTP；审核阻塞/未知状态 |
| 运行审计 | 阶段记录、尝试次数、租约、结果、Worker 心跳、人工处理入口 |
| 界面 | 本地工作台新增每日调度、Worker 状态、发件箱；Next.js 也提供对应组件源码但未完整构建验收 |

本轮选择**小型 SQLAlchemy 数据库队列**，没有引入 Celery、Redis 或 LangGraph。保留 `TASK_MODE=local` 兼容路径；不加 `--durable` 时仍是原本单 API 进程任务模式，不能启用定时执行。仅推荐一个本地 API 和一个 Worker；并发单元测试不等于集群部署认证。

## 6. 直接运行独立进程

用于进一步部署验证时，API 与 Worker 必须读取**相同数据库、模式、模型和邮件配置**。先停服迁移和初始化，再在两个终端分别执行（目录为 `backend`）：

```bash
# 先在 backend/.env 设置 TASK_MODE=durable 和相同 DATABASE_URL
python -m alembic upgrade head
python -m app.cli init

# 终端 A
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
# 终端 B
python -m app.worker
```

生产进程管理器、持久服务器、TLS、出站策略和告警需要另行配置。`python -m app.worker --once` 只完成一次扫描，最多运行一项任务与一项投递，不是常驻调度服务。

## 7. Next.js 前端与部署配置

```bash
cd apps/web
cp .env.example .env.local
npm install
npm run typecheck
npm run build
npm run dev
```

然后打开 `http://127.0.0.1:3000`。源码使用 Next.js / React / TypeScript / Tailwind / TanStack Query，包含原有工作台和新增自动化页面。

**本轮只完成 11 个 TS/TSX 文件的语法检查，未完成依赖解析、完整类型检查、构建或 Next.js E2E。** npm 安装受阻，没有捏造 lockfile。`compose.yaml` 已加入 Worker，PostgreSQL override 同步数据库配置；只验证 YAML 解析，未执行 Docker/PostgreSQL。GitHub Actions 只是配置，没有远程运行成绩。

## 8. 复跑质量验证

```bash
python -m pip install -r backend/requirements-dev.txt
cd backend
python -m pytest -q --cov=app --cov-report=term-missing
cd ..
python scripts/durable_acceptance.py
```

后端本轮 **206 passed**，行覆盖率 **91.47%**；覆盖率范围仅本次 pytest 收集的 Python app 语句，不包括单独子进程、浏览器和真实网络。原版 121 项测试在未修改基线上全部通过；新发现的四项缺陷在原版全部失败、修复后通过。不能用旧测试通过代替代码审计。

实际进程故障脚本会建立临时 replay 库，强制终止已领取任务的子进程，等待真实租约过期，再启动新 Worker 恢复；没有向外部发信或调用模型。

旧版迁移复验需要另外解压原始 v0.1 源码：

```bash
python scripts/migration_acceptance.py --baseline /path/to/unpacked/v0.1/ai-pulse
```

浏览器验收需先运行 `python scripts/run_local.py --demo --durable`，并安装 Chromium：

```bash
python -m playwright install chromium
python scripts/browser_acceptance.py
```

本轮环境阻止原生浏览器访问本地服务，实际执行的是 `python scripts/browser_acceptance.py --bridge`，**18 项通过**。桥接页面 JS→Python HTTPX→真实 API/SQLite/Worker，仅验证这种路径，不能视为原生浏览器 HTTP/CORS、Next.js 或完整端到端上线验收。

## 9. 安全与后续门禁

这是单用户本机开发工具。默认绑定 `127.0.0.1`，可用 `ADMIN_TOKEN` 保护所有业务 API；健康检查无认证。不具备多租户隔离。不要直接公开暴露无令牌接口、SMTP 配置或本地数据目录。

原始网络内容是不可信数据，模型没有外部写工具。固定来源允许列表与 DNS 检查不是完整出站隔离；模型费用、提示注入和真实事实质量仍需进一步验收。任务恢复是已提交阶段的恢复，**不是模型流式中间状态恢复，也不保证外部动作全局恰好一次**。

本次没有写入用户的 GitHub、本地电脑或线上服务。源码、验证报告与截图通过此压缩包交付。下一步的硬门禁是实际来源、真实模型、Next.js 构建及真实投递的验证，再评估研究 Agent、向量检索、MCP/Skills，而不是继续堆叠空框架。
