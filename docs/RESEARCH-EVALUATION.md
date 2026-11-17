# 可复现研究检索评测 v2

运行：仓库根目录 `cd backend && ../.venv/bin/python -m app.research_eval --output /tmp/research-evaluation.json`。CI 后端运行同一命令，上传 `synthetic-research-evaluation` JSON artifact。默认 k=5；仅支持 1–5，以保持两个入口相同返回预算。

## 数据与隔离

`backend/tests/fixtures/research_eval_synthetic_v2.json` 冻结 8 个手写合成快照和 12 个问题：中文、英文、Unicode、跨来源、当前/历史版本、跨语言、禁用来源、live/replay 隔离、无答案及词汇重叠干扰。全部是虚构资料，`.invalid` URL；标签由开发者编写，**不是独立人工裁决或真实世界保留测试集**。运行器只接受明确 synthetic 的数据，在新建 SQLite 内存库运行；不接受数据库 URL、不读取应用配置、不调用模型、embedding 或 SMTP。真实数据需另行授权并建立出处/标注协议，不可改标签伪装真实集。

报告包含数据 SHA256、每个问题排名、完整引用、偏移、模式/降级原因、调用数和本地计时。允许作用域由 fixture 独立计算，不以生产 scope builder 当真值。

## 本次冻结基线（k=5）

| 指标 | lexical | 请求 hybrid |
|---|---:|---:|
| 有答案问题 Recall@5（7题） | 6/7 | 6/7 |
| MRR@5（7题） | 6/7 | 6/7 |
| 无答案检索拒答率（5题） | 3/5 | 3/5 |
| 引用字符位置一致率（11条） | 100% | 100% |
| 作用域越界条数 | 0 | 0 |

**请求 hybrid 实际全部降级 lexical**，原因 `vector_database_unavailable`，embedding_calls=0。这只比较配置降级行为，不能证明向量质量相同或优于 lexical。真实 embedding 未运行；不用随机/fixture 向量冒充语义质量。

已保留的失败：中文“工具调用”找不到英文 invokes tools；无答案“青岚模型售价多少钱”返回主题相关但不支持价格的材料；历史资料“十次”因共享中文二元词“十次”匹配“二十次”。历史 scope 没越界，但这**不是时效事实已验证**。不通过删失败题或为这几个词定制阈值美化分数。

## 指标解释与门槛

Recall/MRR 仅有答案题进入分母，无答案单独统计，不赋虚假满分。无引用时字符一致率为 null，不视作100%。本地 latency_ms 只供观察，不作为跨机器性能门槛。CI 冻结当前完整基线及精确引用/隔离不变量；质量提升须连同新基线和案例审核，不称当前质量已达产品发布要求。

本批还修复旧 `retrieval_eval.assert_thresholds` 的 NaN 绕过（比较均为 False 导致假通过）、无穷/非法比例、bool/非整数 k 以及直接调用 evaluate_cases 的重复 ID 加权。新回归修前18失败、修后通过。

## 人工评审与下一步

JSON 每题 `human_semantic_review` 留空、状态 not_reviewed。审核者需逐题读问题、返回引文与原快照，填写 support（supported/partial/unsupported）、notes，并核对时间限定及跨来源矛盾；另行记录审核者、时间与分歧裁决。自动字符一致率不能填进人工语义支持率。此运行器不生成答案，所以无依据**回答**率没有测量，不可用检索拒答率替代。

下一步：经授权建立独立真实中文问题/原文集与人工金标；对同一 scope、同一 k 配置真实语义 embedding，记录模型/版本/费用/失败和耗时；再讨论召回、拒答与成本阈值。当前小型合成集不是阶段3整体完成。
