# 公开真实短摘录评测：NIST v1

## 实际交付

新增独立的 `public_excerpts` 数据入口，保存3个NIST官方页面的连续可见文本摘录，总共43个英文词，每个来源不超过25词。材料是真实公开文本，问题及标签由AI编写，**不是人工金标、独立留出集、真实用户查询或全面真实世界评测**。仅是可复现的微型冒烟测试。

2026-11-17读取官方页面并检查以下来源：
- [AI RMF概览](https://www.nist.gov/itl/ai-risk-management-framework)：自愿使用描述。
- [AI RMF发布活动](https://www.nist.gov/news-events/events/2023/01/nist-ai-risk-management-framework-ai-rmf-10-launch)：发布日期片段。
- [SSDF 1.1官方摘要](https://csrc.nist.gov/pubs/sp/800/218/final)：减少已发布软件漏洞的目标。

URL、归属、定位说明、读取日期、摘录hash、提取方式及使用依据保存在fixture和报告。标题是AI描述性标签，**不是NIST原始标题**。引用偏移以保存的短摘录为基准，不是HTML/整页偏移。活动页创建日期与发布活动日期分开记录，未知网页发布日期不冒用框架发布日期。

## 许可与最小复制

依据[NIST Copyrights & Disclaimers](https://www.nist.gov/copyrights-disclaimers)的“Use of NIST information”：除单独标注版权的材料外，站内信息可复制、分发，并请求适当署名。所选文字读取时未见单独版权声明；仅保存少量文本，不保存图片、徽标、视频、整页HTML或PDF。此为AI辅助检查记录，不是法律意见或自动许可认证。不得把“NIST站点”直接等同于所有嵌入第三方素材都可重用。

这些摘录按NIST说明归属NIST；仓库代码许可不应被理解为重新许可第三方文本。增加新来源前须核查页面实际权利说明、记录来源与归属，并限制复制。当前loader只接受登记的NIST域名及明确使用依据；域名/hash检查本身不证明许可或来源真实性。自动CI只检查冻结数据，不访问网站或自动刷新许可。

## 隔离与实际改进

过去评测仅支持合成资料，seed还会把引用地址写成example.invalid。现在独立入口要求public_excerpts来源，并将真实source_url保留到引用。默认合成loader仍拒绝真实摘录，避免把真实内容伪称synthetic。每次新建隔离SQLite内存库；数据库使用replay命名空间表示离线执行，不表示文档内容是合成的，报告单独明确provenance。

增加来源/hash/署名/提取位置/使用依据守卫，拒绝超长摘录、未审核域名、含凭据URL、重复来源和冒称人类标注。**未改变生产检索策略**，没有凭三份短文调阈值。

## 初始结果及不可宣称的内容

9题：6题有答案、3题无答案。lexical Recall@5=4/6，MRR@5=3.5/6，无答案检索拒答=1/3；12条返回引用字符一致，scope越界0。两个纯中文跨语言问题未召回，主题重叠无答案仍返回材料，保留失败。

hybrid请求在SQLite全部降级lexical，真实embedding未运行；没有模型回答或付费调用。无答案仅表示所保存摘录不支持，不能推断完整网页或全网无答案；“减少漏洞”也不意味着保证消除所有漏洞。字符一致率不等于语义支持率，实际人类审查尚未执行。

复现：`cd backend && ../.venv/bin/python -m app.public_evidence_eval --output /tmp/public-evidence.json`。CI单独产出 `public-evidence-evaluation` artifact，内含 `public-evidence.json`；与合成评测报告分开。

下一步需要更广来源、逐来源许可复核、独立问题/语义标注及真实embedding对照。当前微型集不支持宣布真实检索质量验收完成。
