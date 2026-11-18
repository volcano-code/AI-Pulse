# 阶段 0/1：基线、状态合同与部署入口

## 基线

- 检查日期：2026-11-17。
- 基线 main：`2ebcb4a494e22f2e68df09b8bd345324c4142130`。
- 本轮在独立云端 checkout 开发；未修改用户本机、数据、密钥，未合并 main。
- 基线后端独立重跑：432 passed、11 PostgreSQL-only skipped，28.38 秒。

## 改动

- `/api/v1/status` 的 `capabilities.vector_search` 表示实现支持，不再硬编码为 false。
- 新 `retrieval` 状态分离 requested_mode、embedding_configured、数据库/schema/pgvector/语料向量前置条件和调用授权。
- 配置检查不调用模型，不回填向量，不发送邮件；诊断查询在独立事务内，错误只返回安全机器码。PostgreSQL 单条诊断 SQL 有 1 秒 statement timeout。
- 尚未探测的 schema/pgvector/corpus 项为 `null`，不是失败。
- `provider_health=not_probed`；`local_prerequisites_ready` 只代表数据库级本地前置条件，不保证当前问题的作用域、在线服务或结果准确性。
- Compose 显式传入检索配置，新增 v2 wrapper；文档不依赖历史 shell 函数，不输出展开密钥。
- README 与下一阶段清单更新；历史验收保留原义，没有把历史记录包装成本轮结果。

## 本轮验证

- 新状态与 Compose 入口共 11 项单元测试；后端全量：443 passed、11 skipped，28.05 秒。
- PostgreSQL 测试增加 fixture 语料下状态检查；本地没有 PostgreSQL/Docker，须由对应 CI gate 验证，不能把 skipped 计作成功。
- Compose wrapper 在本环境实际运行，因缺少 Docker 明确退出 2；它的命令构造单测不是实际 Docker 部署验收。
- 本地 `npm ci`（使用可写 /tmp cache）、TypeScript typecheck、Next production build 已通过；首次默认 npm cache 不可写，调整 cache 后重跑成功。未独立运行原生浏览器，等待 browser-e2e CI。
- 未调用真实付费 LLM/Embedding、未发 SMTP、未作生产部署或用户本机验收。

## 后续放行条件

提交后核对四项 exact-commit CI（backend、postgres、frontend、browser-e2e）。本地部署、真实服务小额 canary 和长期运行仍是独立门槛。阶段 1 仅完成代码及文档修复，不宣称本机 Docker 闭环已通过。

## 安全门禁发现与修复

首次本 PR CI 的 frontend audit 拦截了既有 Next 16.3.8 的公开漏洞（包括 GHSA-2464-fhr4-w9xw）；未绕过 audit。升级到官方已发布 Next 16.4.6，更新 lockfile，本地 audit/typecheck/build 重跑通过。审计零告警仅代表当次 registry 数据，不是安全证明。最终升级提交仍需重新核对四项 CI，不能复用升级前结果。

- [Next 16.4.6 release](https://github.com/vercel/next.js/releases/tag/v16.4.6)
- [GitHub advisory](https://github.com/advisories/GHSA-2464-fhr4-w9xw)
