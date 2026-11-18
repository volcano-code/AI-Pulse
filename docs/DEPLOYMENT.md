# 部署与故障排查

## 不依赖 shell 函数

此前的 `unknown shorthand flag: 'p' in -p` 不能仅凭一行日志确定根因。先运行 `docker compose version`，再用项目的 `scripts/compose.py`，避免遗失或错误定义的 `dc`/`logrun` 函数。脚本固定调用 `docker compose -p ai-pulse -f ...`，不会运行 `docker -p ...`；检查插件和 `.env`，原样返回 Compose 退出码。它不安装 Docker、不修改系统权限。

从仓库根目录：

```bash
python scripts/create_compose_env.py
python scripts/compose.py --postgres -- config --quiet
python scripts/compose.py --postgres -- up -d --build --wait
python scripts/compose.py --postgres -- ps
```

已有根 `.env` 时跳过创建步骤；脚本拒绝覆盖。`backend/.env` 用于非 Docker 启动器，根 `.env` 用于 Compose；不要混淆。生成器默认时区 America/Los_Angeles，可在自己的根 `.env` 中改成所需 IANA 时区，例如 Asia/Shanghai；计划启用前核对页面时间。

不用 PostgreSQL 时省略 `--postgres`，使用独立 SQLite volume。不要在两种数据库之间来回切换并误认为数据自动迁移；没有提供 SQLite→PostgreSQL 自动搬迁。

数据库密码由生成器使用 URL-safe 随机字符创建；自行替换密码时需考虑数据库 URL 转义。不要公开 `.env`。`config --quiet` 验证配置而不打印展开的密钥；普通 `config` 会展开值，不能贴入公开问题或 PR。

## 检查与停止

- `docker compose version` 失败：安装官方 Docker Compose v2 插件；`docker --version` 成功不证明 Compose 已安装。
- Docker daemon 不可达：确认服务与当前用户访问权限；不要自动放宽 socket 权限。
- 端口冲突：检查已有 8000/3000 服务，确认身份再停止；不要批量杀进程。
- API 健康检查失败：在本机查看 `python scripts/compose.py --postgres -- logs --tail 100 api`；分享前脱敏。数据库迁移失败应先备份和诊断，不要 stamp head 绕过。
- 正常停止：`python scripts/compose.py --postgres -- stop`；保留数据 volume。不要执行 `down -v` 删除唯一数据副本。
- 文件模式的投递留在应用 data/outbox 中，不会发邮件；SMTP/模型需显式配置与验收。

## 验收门槛

1. 使用新建测试部署，保留已有生产数据。
2. API health 正常、前端可访问、Worker heartbeat 可见。
3. 完成采集、简报、引用核查、文件投递；失败保留真实原因。
4. 重启后历史数据可读，不重复生成或投递。
5. 单独记录真实模型/Embedding/SMTP 的授权与结果。

本轮 wrapper 单元测试模拟 Docker CLI，不能替代实际 Docker daemon、镜像构建、volume 或本机验收。PostgreSQL CI 使用一次性服务，不代表你的生产部署已通过。

## 本地 SQLite 升级保护

`run_local.py` 读取当前源码的 Alembic head，而不是固定某个历史版本；已有 SQLite 数据库版本不同（包括旧 `0003` 和无版本标记）时，先用 SQLite backup API 建立独立 `.bak` 并执行 `quick_check`，随后才允许迁移。已在当前 head 的库不反复创建副本。备份以独占文件名创建，在 POSIX 上权限为 0600；备份失败会阻止本次启动迁移。

这不替代人工离线备份或完整恢复演练：升级前仍须停止所有 API/Worker，不自动恢复、不删除历史副本，也不对 PostgreSQL 创建本地备份。此保护只用于 `run_local.py`；直接运行 Alembic 或 Docker entrypoint 之前，操作者仍需自行备份。

安全启动器只支持普通 SQLite 文件路径；`file:`/`uri=` URL 明确拒绝，避免 URI 与备份路径解析不一致而绕过保护。

## 隔离的恢复与容器验证

`test_backup_restore_to_new_file_can_upgrade_again`（在 `test_local_migration_backup.py` 中）用一次性 SQLite 库完成 0003→备份→升级→恢复到第三个新文件→再次升级，检查中文内容、版本、完整性与原文件不变。恢复演练不覆盖源库或备份；实际恢复前仍应停服，并在另一个新路径验证。

CI 的 `docker-smoke` 对 SQLite/PostgreSQL 分别构建真实 API/Worker/Web 镜像，在随机项目及全新 volume 内以 replay/extractive/file 模式启动，验证前端及其 API 代理、Worker heartbeat、重启后内容数量保留。运行时网络标记为 internal，禁止应用容器对外采集/调用模型/发邮件；仅构建阶段下载公开依赖。Web 镜像通过 `npm ci` 严格使用提交的 lockfile。

`scripts/docker_smoke.py` 只允许一次性 GitHub-hosted runner，结束时仅删除本次随机项目的 volume。不要移除保护后在用户机器运行。本地工作区无 Docker 时，不把静态检查声称为 Docker 验收；实际结果看该提交 CI。这个 smoke 不验证真实供应商/SMTP、用户机器端口或生产库备份，也不替代完整业务与灾备验收。

Docker internal 网络在部分引擎上不发布宿主机端口，CI 因此从 API 容器通过私有 Compose 网络真实 HTTP 访问 Web 和 API 代理。它验证服务间网络，但不声称验证宿主机 127.0.0.1 端口发布。


## 已有部署升级

部署已有数据前必须先执行[集成升级顺序与回退检查](INTEGRATION-20261118.md)：停API/worker、验证备份、副本升级，再恢复服务。Docker入口迁移没有自动备份，0011 downgrade会删除投递期限，不是无损回退。
