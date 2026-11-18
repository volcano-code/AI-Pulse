# PR24–35 集成验收与升级边界

## 集成对象

本分支从 PR35 的已验证提交 `99f3f863a0fe52339ad271e0db6a703dfe8c944a` 分出，直接面向 main 的基线 `2ebcb4a494e22f2e68df09b8bd345324c4142130`。12个PR本来就是连续祖先链，全部代码已包含在栈顶；不重复cherry-pick，不重写旧分支，也不关闭原PR。精确提交与父链见 `INTEGRATION-MANIFEST.json`。

这提供一个可整体审查、运行CI的main集成候选，而不是已合并或发布。主分支、现有PR、用户电脑/数据库均未改动。以后若main或原PR有新提交，需重新对比、处理差异并重跑最终候选CI，不能以本记录覆盖新工作。

## 集成验收门槛

- `python scripts/verify_integration.py`：只读确认main基线与12个审核HEAD都属于当前HEAD祖先、栈链接一致；需要完整git历史。祖先关系不证明后续未revert或代码正确，仍须diff审查和行为测试。
- 后端全套；新增0009旧库→启动器备份→最新head的真实SQLite演练，逐列比较旧FetchRun/Delivery历史、验证新nullable字段、索引及外键/完整性，备份仍保持0009。
- 前端lockfile安装、TypeScript检查、生产build；CI还有PostgreSQL、原生浏览器、SQLite/PostgreSQL两种隔离Docker运行/重启验证。
- 合成与公开摘录评测分别输出报告；公开历史BriefItem修复为保留来源名称/URL，避免写死synthetic/example.invalid。相关回归检验真实来源及原文不丢失。

本地cloud没有Docker/PostgreSQL服务，因此这两类运行结果以最终SHA的GitHub CI为准。本批新增完整旧数据升级演练是SQLite，不把它等同于PostgreSQL生产历史迁移验收。精确最终测试数和CI链接写在集成PR验收评论，避免文档先行宣称未完成检查。

## 已有部署升级：先停写入，再备份，再迁移

以下是操作顺序说明，本次没有替用户执行：

1. 记录原提交/镜像、配置、数据库位置及未完成投递状态。停止旧API和worker，确认没有其他写入者；不能只停web。尤其0009→0010包含SQLite表重建，不支持旧worker与迁移并行。
2. 对停写的真实数据库做可验证备份。SQLite可使用backup API/一致性备份并对备份执行完整性检查；PostgreSQL应使用适配实际版本的数据库备份并在隔离库恢复验证。保留配置/凭据的安全副本，不上传公开仓库。
3. 本地启动器 `run_local.py` 有SQLite迁移前备份；**Docker entrypoint和直接alembic没有这层自动备份**。不能把本地启动器保证套到Docker或PostgreSQL。
4. 先在副本上验证0009→head，再选择已审查集成镜像部署。API entrypoint执行迁移；确认迁移成功及服务状态后才恢复worker。不要让旧、新worker并行处理队列。
5. 复核旧pending/unknown投递再恢复外发。旧Delivery.expires_at仍为NULL，不猜测历史期限；旧无owner的FetchRun也不会自动冒认租约归属。保留原状态不意味着旧pending邮件适合现在发送。

## 回退不是直接downgrade

0011的downgrade会删除expires_at；旧代码可能重新提交尚pending但按新期限已过期的投递。0010也会移除owner/fencing信息，因此**不能将alembic downgrade视为无损线上回退**。

失败时保持API/worker停止，先封存故障库，在隔离环境用已验证备份和原代码恢复/检查，再由负责人决定回退。恢复备份会丢失备份后的写入，外部已发送消息无法撤回；unknown投递必须人工核对，不能盲目重试。禁止为“恢复”清空卷或覆盖唯一数据副本。

## 尚未完成

集成CI不等于用户环境生产验收；真实付费LLM/embedding、SMTP、长期运行、独立人类语义标注尚未完成。公开资料仍是43词/9题微型测试，之前词面过滤实验未上线。后续合并/发布需明确选择，当前只提交安全的集成候选。
