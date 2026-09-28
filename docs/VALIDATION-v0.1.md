# AI Pulse v0.1.0 · 本轮验证报告

## 结论与范围

本轮交付的是第一阶段基础工程：可运行的 Python 后端、本地验收页面、Next.js 前端源码、数据库迁移、测试和部署配置。**不是八周完整 MVP，不是已经上线的自主研究 Agent。** 真实来源连通性、真实模型生成、Next.js 完整构建三个关键验收项仍未完成，不能声称真实联网闭环已经验收通过。

代码与测试在本轮会话的容器环境实际执行；没有向用户的本地文件系统、GitHub 或线上服务器写入任何内容。交付方式是源码压缩包。

## 实际执行结果

| 验证项目 | 本轮结果 | 证据与限制 |
|---|---|---|
| Python 单元、契约与 API 集成测试 | **121 项通过，0 项失败** | [pytest 输出](validation/pytest.txt)、[JUnit XML](validation/pytest.xml)；包含合成资料、内存/临时 SQLite 与模拟模型响应 |
| Python 行覆盖率 | **93.87%，终端四舍五入显示 94%** | 946 条可执行语句，58 条未覆盖；[coverage JSON](validation/coverage.json)。不是事实准确率、分支覆盖率或生产可靠性指标 |
| 本地验收 UI 浏览器交互 | **13 项通过，0 项失败** | [逐项结果](validation/browser-acceptance.json)。原始 HTML/CSS/JS，通过 Python HTTP 桥连接真实本地后端，不是 Next.js 页面 |
| 浏览器未捕获 JS 错误 | **0 项** | 在本次所执行的 13 个交互检查范围内；不代表覆盖所有操作路径 |
| TS / TSX 语法检查 | **10 个文件，0 个语法错误** | [检查输出](validation/typescript-syntax.json)；使用环境已有 TypeScript 5.8.3。不是依赖解析、完整类型检查或 Next.js 构建 |
| 静态验收 UI JavaScript | `node --check` 通过 | 语法检查；交互另由浏览器检查覆盖 |
| SQLite 迁移 | upgrade、模型漂移检查、downgrade、再次 upgrade 通过 | [迁移输出](validation/migration.txt)；只针对临时 SQLite 数据库 |
| 本地启动脚本 | 新建数据库、迁移、播种样本、生成简报、启动服务通过 | [启动日志](validation/startup-smoke.txt)；运行 `python scripts/run_local.py --demo`，浏览器验收连接这个服务 |
| 接口合同 | 已从实际 FastAPI 应用导出 OpenAPI JSON | [接口合同](../contracts/openapi.json)；不是前后端类型完全一致的证明 |

环境版本见 [environment.json](validation/environment.json)。测试没有把运行环境的网络限制转化为产品成功指标。

## 浏览器验证的准确含义

本轮 Chromium 直接访问本地服务器时被环境策略阻止，因此验收脚本使用显式的 `--bridge` 模式：加载原始验收页面 HTML/CSS/JS，由浏览器脚本调用 Python 绑定函数，再通过 HTTPX 请求正在运行的本地 API。服务返回值来自真实后端、真实 SQLite 存储，不是预先写好的接口响应。

这验证了实际页面逻辑、DOM 交互、SSE 消息解析与后端读写，但**没有验证浏览器原生 HTTP 网络栈、跨域、原生 sessionStorage 行为、Next.js 路由或 Next.js 生产构建**。桥接模式仅存在于测试脚本，不是应用运行要求，也没有修改环境的浏览器网络策略。

13 个检查涉及：合成数据标记及六条卡片、证据位置高亮、对话框关闭、收藏持久化、来源开关、时区偏好、流式检索引用、无匹配拒答、历史简报、生成任务与阶段记录、390px 窄屏无横向溢出、未捕获 JS 错误检查，以及与 Python 证据偏移一致的 Unicode 码点切片。

截图均为**合成测试资料下的本地验收页面**：

- [桌面工作台](screenshots/dashboard.png)
- [证据抽屉](screenshots/evidence.png)
- [窄屏布局](screenshots/mobile.png)

Next.js 独立测试文件在 `apps/web/tests/workspace.spec.ts`，本轮未运行。不能将上述 13 项计入 Next.js E2E 成绩。

## 未通过或未执行的验证

| 项目 | 实际情况 | 下一步完成条件 |
|---|---|---|
| 三个真实来源采集 | Hugging Face Blog、arXiv、GitHub Releases 均在解析 DNS 时失败，任务状态为 failed | 在有外网的环境运行真实采集，逐源检查有效内容、时间和解析结果 |
| npm 安装 | 请求 npm registry 时出现 `EAI_AGAIN` | 联网安装，生成并审核真实锁文件 |
| Next.js 完整前端验收 | 未安装依赖，未运行完整 typecheck、build 或 Next.js E2E | `npm install` → `npm run typecheck` → `npm run build` → 端到端测试 |
| 真实模型调用 | **0 次**；只有 MockTransport 契约测试 | 使用用户可用的模型 ID、API 端点和密钥做单条实测，确认格式、费用、引用与人工审核 |
| Docker Compose | 环境没有 Docker，未执行 | 本地或 CI 启动服务、执行健康检查和挂载持久化验证 |
| PostgreSQL | 提供 SQLAlchemy URL、可选依赖与 Compose 覆盖配置，未运行数据库 | 安装驱动并在 PostgreSQL 上执行迁移与集成测试 |
| GitHub Actions | 提供 CI YAML，未创建远程仓库或运行工作流 | 将源码推送到用户确认的仓库，再读取实际 CI 结果 |
| 自动推送、定时调度、长时间连续运行 | 本轮未实现或未执行 | 后续独立开发和验收，不能用手动成功生成代替 |

真实采集尝试记录见 [live-ingest-attempt.json](validation/live-ingest-attempt.json)，npm DNS 失败见 [npm-network.txt](validation/npm-network.txt)。未将合成数据填充进 live 数据库，也没有把摘录模式伪装成模型调用成功。

## 已覆盖的重点行为

后端测试覆盖来源解析、XML 实体拒绝、链接规范化、精确去重、不可变快照、发布时间窗口、未知时间和未来资料排除、来源停用、用户偏好、历史证据和元数据保留、幂等指纹、收藏、任务状态、错误释放、访问限制、模型格式/引句/截断检查、人工审核门禁、词项检索与证据不足拒答等。

这些是工程行为验证，**不等于全面红队、语义事实核验、性能压测、生产安全认证或新闻质量评测**。模型摘要包含一条匹配的原句，不代表摘要里的所有陈述均获得支持；当前模型产物默认待人工审核。

## 验收状态分类

**已实现并在本环境执行验证：** 本地 SQLite 后端、合成资料处理、原文摘录简报、不可变证据、偏好/收藏/历史、词项检索、运行记录、本地验收 UI 的桥接交互、SQLite 迁移。

**源码或配置已提供，但真实集成未验收：** 实际来源抓取、真实模型生成、Next.js 构建与浏览器 E2E、Docker、PostgreSQL、远程 CI。

**尚未实现：** LangGraph 自主研究、多 Agent、Celery/Redis 持久队列、自动定时推送、语义事件聚类、跨日增量事实、向量 RAG/Reranker、MCP/Skills、长期偏好学习、多用户权限隔离、Langfuse/OpenTelemetry。

## 复现方式

启动、模式切换、测试命令和限制统一记录于 [README](../README.md)。建议在有网络的环境首先完成真实来源、真实模型和 Next.js 三个验收缺口，然后再继续引入更复杂的 Agent 编排。
