# AI Pulse v0.2.0 · 质量复检与阶段推进结论

## 结论

**原 v0.1 不能直接判定为质量合格：121 个旧测试虽全部通过，但定向审计复现了四项缺陷。修复并补充回归测试后，本地开发门禁通过；随后完成 v0.2 的持久任务、每日调度与发件箱。**

**完整外部集成门禁仍未通过，因此没有放行线上部署，也没有把本轮称为完整每日研究 Agent。** 新功能只在明确隔离的合成数据、本地文件投递和受控模拟外部接口下验收。真实来源、真实 LLM、Next.js 构建、真实 SMTP 是未关闭项，而非豁免成功项。

本报告区分四种证据：未修改基线的复跑、原版失败用例、修复后回归、独立进程/浏览器/迁移验收。原始输出均随包保留。

## 1. 原版核验

原压缩包 89 个文件，`MANIFEST.sha256` 的 88 个被列出条目全部匹配。解压后的原代码运行 **121 passed**。原版报告的 93.87% 行覆盖率得以复现，但旧测试未覆盖以下边界。

[完整性核验](validation-v0.2/baseline/baseline-manifest.json) · [基线测试输出](validation-v0.2/baseline/baseline-pytest.txt) · [基线 JUnit](validation-v0.2/baseline/baseline-pytest.xml)

## 2. 四项实际缺陷：先红后绿

| 编号 | 原版行为与风险 | 修复策略 | 回归测试 |
|---|---|---|---|
| Q01 | 另一 feed 提交同 URL 的不同内容，可覆盖文章/快照却保留原来源 ID，造成证据归属错误 | 拒绝跨来源覆盖同一规范化 URL；暂不做跨源关系合并 | `test_other_source_cannot_overwrite_provenance` |
| Q02 | feed 可解析但条目全部无效，仍标成抓取成功和来源健康 | 有效新增/更新/未变化条目为零时回滚入库并记录失败 | `test_all_rejected_entries_do_not_mark_source_healthy` |
| Q03 | 仅成功轮询时间改变、内容不变，也进入简报指纹，产生重复版本 | 从身份指纹中移除成功轮询时间，原简报仍保存当时健康信息 | `test_successful_poll_timestamp_alone_does_not_make_new_edition` |
| Q04 | 文章早已发现，但当前快照是在简报截止之后采集，仍会进入截止时点的候选 | 要求快照捕获时间不晚于 cutoff；无法重建的项保守排除 | `test_snapshot_captured_after_cutoff_is_not_used` |

这四项在未修改 v0.1 上均实际失败，而不是只根据阅读推断。见 [原版红灯输出](validation-v0.2/baseline/regression-red.txt)。Q01–Q03 修复时完成过 **124 passed** 的中间门禁，见 [当时输出](validation-v0.2/baseline/quality-gate.txt)；Q04 后续加入并包含在最终 **206 passed** 中。

Q04 的修复不等于完整历史重演：当前选择最新合格快照，不提供完整双时间历史元数据重建。一个原有测试辅助函数同步补齐“历史 first_seen 与 snapshot captured_at”的一致时间，没有删除旧断言来让测试通过。

## 3. 下一阶段实际交付

新增数据库持久任务与独立 Worker，支持幂等提交、领取互斥、租约续期、旧令牌写入隔离、有限退避重试和已完成阶段检查点。API 重启不再重置 durable 任务。对可能已经收费的模型调用结果不明，任务进入 `needs_attention`，不盲目自动重放。

新增按 IANA 时区和当地时间运行的每日流程，默认关闭；同一日期不会因多次扫描或更改时间而重复调度。夏令时缺失分钟顺延，重复小时取第一次；只补当前日期，过期历史任务不悄悄迟发。

新增与任务完成同事务的发件箱、不可变邮件正文和 Message-ID、审核阻塞、本地 `.eml` 输出及可选 SMTP 适配。SMTP 结果不明进入 `unknown`，需要明确人工判定。发件箱的自动安全重试和本地文件崩溃恢复均有次数上限。

新增本地/Next.js 自动化页面源码、Worker 心跳、调度设置、发件箱和人工处理入口。**Next.js 页面未获得运行验收。**

技术实现为 **SQLAlchemy/SQLite 小型队列**，不是 Celery、Redis、Temporal 或 LangGraph。主动研究、多 Agent、语义聚类、跨日事实增量、向量 RAG、MCP、Skills、长期学习和多租户权限仍未实现。

## 4. 最终执行结果

| 验证 | 结果 | 能证明什么 / 不能证明什么 |
|---|---|---|
| 后端 pytest | **206 passed，0 failed** | 单元、契约和本地 API/SQLite 集成；不是 206 次真实外部调用 |
| Python 行覆盖率 | **91.47%：1522 / 1664 语句** | pytest 范围内覆盖，142 行未覆盖；不等于事实准确率、分支或生产可靠性 |
| 本地工作台浏览器 | **18 passed，0 failed，0 未捕获 JS 错误** | 原页面 JS 经 HTTP 桥访问实际 API/Worker/SQLite；非原生网络、非 Next.js |
| 真实进程故障注入 | **4 项断言通过** | 子进程领取任务后被强杀，新 Worker 在真实租约过期后以第 2 次尝试恢复，只留下 1 个本地文件；不覆盖所有崩溃位置 |
| v0.1→v0.2 SQLite 迁移 | **通过，旧值完整保留** | 原始 6 篇文章/6 个快照/1 份简报/6 个条目/1 个任务/3 个来源/1 个 workspace 的旧列不变 |
| SQLite 降级再升级/漂移检查 | **通过** | 仅临时测试库；不能对正式数据照抄降级 |
| TS/TSX | **11 个文件语法检查通过** | 不是完整 typecheck、依赖解析或 Next.js build |
| 静态 JS / Python compile / YAML 解析 | **通过** | YAML 能解析不代表 Docker 启动成功 |
| OpenAPI | 从实际应用导出 **25 个路径** | 不是前后端类型完全一致的证明 |
| 依赖版本 | 12 个直接运行/测试依赖与固定版本一致 | 不证明干净机器可从当前索引下载安装，也不是完整漏洞扫描 |

最终较原版增加 **85 项测试用例**。其中 4 项为审计回归，其余覆盖任务、调度、发件箱、API 和显式模拟的 live-mode 流程。live-mode 的模拟样本在临时库中明确标为 synthetic，不是联网资料采集成果。

[pytest 原始输出](validation-v0.2/pytest.txt) · [JUnit](validation-v0.2/pytest.xml) · [覆盖率 JSON](validation-v0.2/coverage.json) · [浏览器逐项结果](validation-v0.2/browser-acceptance.json) · [实际进程恢复](validation-v0.2/process-acceptance.json) · [迁移验证](validation-v0.2/migration-acceptance.json) · [依赖版本](validation-v0.2/dependency-versions.json)

## 5. 未放行项与已知限制

| 项目 | 本轮实际情况 | 放行条件 |
|---|---|---|
| 真实 feed / API 抓取 | 再次执行真实模式命令，25 秒外层上限内未完成；无成功内容作为验收证据 | 每个来源有真实成功响应，核对发布时间、内容、URL、更新与重复行为 |
| npm / Next.js | 安装尝试超出受控上限；额外探测显示 npm registry DNS 失败；无 lockfile | 安装真实依赖、生成锁文件、完整 typecheck/build/原生 Next E2E |
| 真实 LLM | **0 次调用**；只有模拟契约 | 用户配置实际端点/模型/密钥，单条事实、usage、审核与异常验证 |
| 真实 SMTP | **0 次提交**；只做契约模拟及本地 `.eml` | 经用户授权向明确收件人单封实测，验证服务端日志、未知状态处置 |
| Docker/PostgreSQL | 无相应执行环境；只提供配置与 YAML 解析结果 | 实际迁移、健康检查、持久卷、并发与重启实验 |
| 7 天连续运行、远程 CI、部署 | 未执行、未上传任何仓库 | 真实运行日志和远程工作流记录 |

网络抓取已有 HTTP 分段超时与响应字节预算，但系统 DNS 解析可能超过这些超时，尚无隔离抓取进程的硬总截止时间。它没有通过网络阻塞恢复验收，不能声称已经解决所有挂起情形。

任务恢复粒度是已提交的阶段；不能承诺模型流式状态恢复或外部操作 exactly-once。SQL 队列只在本轮 SQLite、本机单 workspace 场景验证；并发测试不构成多机分布式队列认证。默认源码不提供多租户身份、公开站点安全、完整提示注入防护、语义事实质量或生产性能保证。

`file_written` 永远不是“已发邮件”；`sent` 只是 SMTP 服务端接受，不是收件箱送达。某些人工判定也无法消除外部不确定性，因此本轮不夸大可靠性。

## 6. 复现与交付

运行入口和配置见 [README](../README.md)。本轮截图见 [自动化工作台](screenshots/v0.2/automation.png)、[手机布局](screenshots/v0.2/automation-mobile.png)、[证据窗口](screenshots/v0.2/evidence.png)。图中均为合成数据。

项目归档包含源码、迁移、测试、报告和原始验证记录；排除运行数据库、个人 `.env`、缓存、依赖目录和用户邮件。`MANIFEST.sha256` 对包内文件作哈希清单，清单自身不纳入清单。最终归档还需以解压副本复跑测试，结果另随交付提供。

**可继续的是有验证边界的本地工程迭代；不可宣称已经完成的是全栈联网闭环、生产上线和完整自主研究 Agent。**
