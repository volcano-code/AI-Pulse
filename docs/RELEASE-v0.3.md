# AI Pulse v0.3.0 · 本轮开发与验收报告

验证日期：2026-09-28。基线：从用户文件库取回的 `ai-pulse-v0.2.0.zip`；本轮在其基础上修改代码，而非从旧描述猜测实现。

## 结论

**完成了一次可交付的本地工程升级，不是完整 12 周产品交付。** 新增阅读版本、原文差异、有界证据研究、工具调用适配、持久审计和抓取硬截止。保留旧版 Worker、每日调度及事务性发件箱。

**本地验证通过；完整真实联网/Next.js/模型/SMTP 集成仍不放行。** 未宣称生产就绪、未部署、未推送 GitHub、未操作用户本机。

## 1. 真实交付

- `reading.py` + `0003` 迁移：已读状态绑定不可变快照，原文对比保留 Unicode 坐标，历史版本不受新内容覆盖。
- `research.py` / `research_model.py`：默认零模型本地证据流程；可选真实模型工具协议，限定两个只读工具，参数、ID、Schema、次数与时间校验。
- `Investigation` / `research_api.py`：持久记录结果和公开执行事件，幂等键、单工作区并发门禁，重启将不明计费任务标记为待处理，不擅自重放。
- `process_fetcher.py` / `fetch_process.py`：旧 HTTP/DNS 抓取进入可杀死子进程，超时后收尸，敏感配置不传入采集子进程。
- 两个本地页面及对应 Next.js 组件源码：增量阅读、研究助手、引用、变化高亮、预算、停止原因、历史记录。
- 回归测试、真实故障注入、数据库升级、浏览器验收、外部探测脚本与原始记录。

不是本轮已实现：语义事件聚类、独立事实核验、自主联网研究、LangGraph/Celery/Redis、向量检索、MCP/Skills、DeepSeek Harness、多租户或公网生产安全。

## 2. 执行结果

| 检查 | 本轮结果 | 证据与限制 |
|---|---|---|
| 完整 Python 测试 | **262 passed，0 failed，21.66 秒** | `validation-v0.3/pytest.txt` 与 `pytest.xml`；相较旧版 206 增加 56 项，不代表 262 次真实外部调用 |
| 新研究/阅读模块专项 | **50 passed，4.31 秒** | `research-targeted.txt`；包括恢复、时间预算、超额并行工具、历史证据 |
| 本地浏览器 | **13 个断言全部通过，0 未捕获 JS 错误** | `browser-bridge.json`；合成资料 + 真实 API/SQLite/Worker，显式 HTTP 桥；不是 Next.js |
| 原生浏览器访问 | **被阻止** | `browser-native.json/.txt`，`ERR_BLOCKED_BY_ADMINISTRATOR`；未隐藏此失败 |
| Worker 强杀恢复 | **4 项检查通过** | `process-acceptance.json`；真实强杀、真实租约等待，新进程第 2 次尝试完成，仅一份本地文件 |
| SQLite 升级 | **通过** | `migration.json`；真实 v0.2 程序生成旧库，升级前后旧表逐值一致，Alembic 漂移检查通过 |
| 抓取硬截止 | **实际子进程测试通过** | `test_process_fetcher.py`；验证超时、退出及 PID 不再存活；真实源探测 3 秒截止也实际触发 |
| TS/TSX 语法 | **12 文件通过** | `typescript-syntax.json`；环境 TS 5.8.3 转译语法，不是 `tsc --noEmit` 或构建 |
| Python / 原生 JS 静态检查 | **通过** | `static-checks.json` |
| OpenAPI | **30 个路径，版本 0.3.0** | 从实际应用生成 `contracts/openapi.json`；路径数不等于完整契约覆盖 |
| 真实来源 | **0 篇成功入库** | `integrations.json`；3 个来源全部失败，没有合成回填 |
| npm 安装 / Next 构建 | **安装 25 秒外层截止，未完成** | `npm-install.json`；先前域名探测为 DNS 失败，无新 lockfile，无 build 成绩 |
| 真实模型、SMTP | **0 次、0 次** | 工具调用与 SMTP 为测试替身契约；本地 `.eml` 不是收件 |
| Docker / PostgreSQL / 远程 CI | **未执行** | 保留配置不等于部署认证 |

首次包含 coverage 的完整命令超过 90 秒上限，只留下不完整输出，保留在 `coverage-attempt-incomplete.txt`。之后无覆盖率的完整测试成功通过 262 项。因此**本版不报告覆盖率**，旧版 91.47% 是历史数据，不能沿用为当前结果。未删除用例、降低断言来制造通过。

测试过程中还发现并修正了两处新测试准备问题：删除接口需要既有 JSON Content-Type 防护头；历史简报测试必须先创建简报。功能代码未为绕过这些契约而放宽。

## 3. 抓取受阻的具体证据

`check_integrations.py --feeds` 在全新临时 live 数据库实际请求三个来源，测试限额为每源 3 秒（产品默认 20 秒）：

| 来源 | 结果 | 耗时 |
|---|---|---|
| arXiv | FetchError | 1.089 秒 |
| Hugging Face | FetchError | 1.096 秒 |
| LangGraph Releases | FetchDeadlineExceeded | 3.012 秒 |

当前代码可以在实际挂起时终止抓取，而不是让整个 Worker 无期限等待。但这只能证明截止行为，不证明这些来源已端到端通过。错误对外做了脱敏，不将所有具体失败都猜测为相同原因。

## 4. 研究流程的语义边界

默认模式：关键词检索 → 必要时透明的关键词扩展 → 原文位置检查 → 保存证据与过程。模型调用为 0，界面如实标注；这不是“没有密钥仍能神奇生成 LLM 研究报告”。

可选 live 模式：真实 `tools` 参数适配器、模型选择工具、限定服务器保存的证据范围、最多 4 次模型及 4 次工具调用、截止和幂等记录。当前验证由脚本化模型与 HTTP MockTransport 提供，不代表实际供应商可用。

证据 ID 必须在当前调查出现，引句必须匹配对应快照；非法引用会被拒绝。但引用匹配不等于语义蕴含，所以所有模型陈述都为 `needs_review`。研究接口没有发送工具，也不能写入正式发布链路。

结果保留审计和历史读取；API 进程重启会将未完成研究标记 `needs_attention`。它没有持久图节点续跑、多实例 fencing 或自动恢复付费调用。只能部署一个 API 进程。

## 5. 数据升级与安全

`0003` 添加两张新表，不修改旧列。升级测试以未改动 v0.2 源码生成旧数据，逐表摘要与行值比较在升级后完全一致。降级/再升级仅在临时库测试；正式升级必须先停服并备份。

所有新增业务接口沿用已有授权依赖及写请求 Content-Type 防护。读取受数据模式和服务端简报范围约束；跨文章快照和未检索证据 ID 均被拒绝。网页内容被视为不可信数据，工具不含 Shell/任意 URL/发送动作。模型进程密钥不放入 argv/env，也不写日志。

这仍不是多租户或生产隔离；DNS 检查和 URL 白名单不替代网络出站政策。没有财务金额硬预算、独立事实核验准确率或真实世界攻击覆盖率的测量。

## 6. 复现入口

```bash
python -m pip install -r backend/requirements-dev.txt
cd backend
python -m pytest -q
cd ..
python scripts/run_local.py --demo --durable
# 另一个终端：
python scripts/browser_v03.py
# 只有明知当前环境受限时才用 --bridge，并保持标注
```

独立脚本：

```bash
python scripts/durable_acceptance.py --output docs/validation-v0.3/process-rerun.json
python scripts/migrate_v03_acceptance.py --baseline /path/to/v0.2/ai-pulse
python scripts/check_integrations.py --feeds
```

本地运行界面截图位于 `docs/screenshots/v0.3/`；包括今日简报、原文变化、研究助手和手机布局。都是合成 replay 数据和桥接验收页面。

## 7. 交付与下一门禁

交付源码 ZIP、README、迁移、测试、截图及机器可读记录；不包含数据库、密钥、`.env`、依赖或真实邮件。哈希清单用于校验包内文件，不证明功能质量。最后解压复验记录随交付单独提供。

后续先解决真实来源和 Next.js 安装/构建，再用用户自有密钥完成单条真实模型工具调用与引用审核；SMTP 只在有明确收件目标与授权后做单封测试。不可把“又加入一个框架”替代这些未通过的门禁。

## 官方实现参考（2026-09-28 核对）

- Next.js 发布与安全公告：https://nextjs.org/blog 。本版依赖改为已发布的 16.3.6；9 月 30 日预告不是当前已可用版本。
- LangGraph 图 API：https://docs.langchain.com/oss/python/langgraph/graph-api 。本版没有声称集成该框架。
- DeepSeek JSON 输出：https://api-docs.deepseek.com/guides/json_mode/ 。JSON 格式不自动证明引用支持或模型兼容性。
