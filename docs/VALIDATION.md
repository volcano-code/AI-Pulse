# v0.2 验证索引

当前结论和所有限制见 [质量复检报告](QUALITY_REPORT.md)。原 v0.1 报告保留在 [VALIDATION-v0.1.md](VALIDATION-v0.1.md)，不能当作本轮新结果。

## 可复查证据

| 项目 | 原始记录 |
|---|---|
| 最终 206 个后端测试 | [终端](validation-v0.2/pytest.txt)、[JUnit](validation-v0.2/pytest.xml) |
| 91.47% 行覆盖率 | [JSON](validation-v0.2/coverage.json)、[XML](validation-v0.2/coverage.xml) |
| 原版 121 测试与四个红灯 | [基线](validation-v0.2/baseline/baseline-pytest.txt)、[红灯](validation-v0.2/baseline/regression-red.txt) |
| 浏览器 18 项（HTTP 桥，非 Next） | [结果](validation-v0.2/browser-acceptance.json)、[输出](validation-v0.2/browser.txt) |
| 实际子进程强杀及恢复 | [JSON](validation-v0.2/process-acceptance.json) |
| 原 v0.1 SQLite 值保留 | [JSON](validation-v0.2/migration-acceptance.json) |
| API + 独立 Worker 启动 | [日志](validation-v0.2/startup-final.txt) |
| TS 仅语法 | [JSON](validation-v0.2/typescript-syntax.json) |
| Python / JS / YAML / OpenAPI | [检查](validation-v0.2/static-checks.json) |
| 运行依赖版本 | [JSON](validation-v0.2/dependency-versions.json) |
| npm 安装受阻 | [记录](validation-v0.2/npm-install.json)、[DNS 探测](validation-v0.2/network-probe.txt) |
| 真实采集受阻 | [记录](validation-v0.2/live-ingest-attempt.json) |
| 外部服务未调用 / 未部署 | [记录](validation-v0.2/external-capabilities.json) |

测试钟与示例中的日期来自容器时钟；合成样例不是相应日期的真实新闻。没有运行真实 SMTP、真实 LLM、Docker、PostgreSQL、Next.js 构建或远程 CI。截图与浏览器测试使用 Python HTTP 桥；不等价于原生网络 E2E。
