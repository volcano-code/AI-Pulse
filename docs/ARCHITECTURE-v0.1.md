# 架构与实现合同

## 一、当前的数据路径

```text
固定来源允许列表
  -> SafeFetcher（HTTPS、公共地址检查、重定向、字节上限）
  -> RSS / Atom / GitHub Release parser（禁用 XML DTD 与实体）
  -> URL 规范化、Article、不可变 Snapshot
  -> 发布时间窗口 + 来源开关 + 偏好排序 + 精确内容去重
  -> 摘录 或 单次模型生成
  -> 原文引句定位
  -> Published excerpt / Needs-review model draft
  -> Brief、BriefItem、人工批准、Markdown 导出
```

`api / cli / browser` 调用同一套服务，不维护一个虚假的演示专用后端。演示和真实数据只在导入入口不同，数据库与最终生成路径相同；模式禁止混合。

## 二、八张业务表

`workspace` 保存单工作台的数据模式和偏好；`sources` 保存来源配置和采集健康度；`articles` 保存规范化 URL 和当前快照指针；`snapshots` 保存不可变文本和 SHA-256；`fetch_runs` 保存每次采集结果；`runs` 保存后台流程状态；`briefs` 保存版本与生成配置；`brief_items` 保存当时的标题、摘要、证据、来源、发布时间与主题。

当前不是完整的原子主张库。每个条目只有一个连续引句作为证据，尚未对摘要中的多条主张分别验证。不要用表名或 API 状态宣称“全面事实核验”。

## 三、时间与内容版本

所有内部时间统一以 UTC ISO 字符串存储，显示时使用 IANA 时区。`published_at` 不存在时留空，不用 `first_seen_at` 替代；未来日期被排除。`event_time` 未实现事件级抽取，保持空值。

快照定位坐标是 **Python Unicode code-point 字符索引**，区间为 `[start, end)`，不是 UTF-8 字节位置，也不是 PDF 坐标。当前客户端 JS 的 `String.slice` 使用 UTF-16 索引，对含非 BMP 字符的原文需要使用 code-point 切片；当前版本以共享转换函数修正该差异（详见对应测试）。

同一 URL 的内容更新产生新快照；旧简报保留旧快照。发布时间及来源字段也在 BriefItem 中冻结，避免历史显示被后续元数据修正改变。身份指纹包含本地日期、偏好、入选快照及元数据、生成模式、模型 ID、提示版本与来源状态；相同指纹复用已有版本。

这是“精确内容去重 + 版本保留”，不是成熟的语义事件图谱或跨日信息增量 Agent。不同语言转载不一定会合并，相同事件的不同文章仍可能同时入选。

## 四、运行与错误处理

`new_run` 创建带唯一 `active_key` 的记录。数据库约束阻止两个同时提交的活动任务；`execute_run` 使用状态比较更新领取 queued 任务，重复调用不会再次执行已完成任务。各阶段日志分事务保存，正文与简报也有独立事务。

来源请求失败后更新 failure_count / last_error，不修改 last_success_at。全部来源失败则任务状态 failed，部分失败为 partial。CLI 对 failed 返回非零状态；后台流程不会把失败视为“已获得新资讯”。

该运行器是本机单进程方案。不是分布式持久执行，也不提供跨进程 lease、心跳或自动恢复。API 重启将活动任务标记为 interrupted，重新提交复用已保存的内容。初始化和播种不应在 API 运行期间执行。

## 五、LLM 与“核验”含义

提示词把原文放入结构化 user 数据字段，不提供工具。模型请求最多一次，不自动重试。模型输出需完成、符合 LLMDraft Schema，而且引句必须是保存文本的精确连续子串。

这些检查只证明引句存在，**不证明中文摘要被引句支持，更不证明发布方说法客观真实**。模型输出的 verification 为 quote_match_only，简报状态 needs_review；正式导出需要用户显式 reviewed=true。原文摘录的 exact_excerpt 同样只是来源表述，不是“已独立证实”。

测试对模型使用 MockTransport。没有调用真实模型或证明真实模型质量。金额未知用 null，不伪造费用；未收到有效 usage 的失败请求可能已被计费。

## 六、检索与 UI

检索遍历最多 1000 个候选文档，使用英文词项及中文双字片段进行 BM25 风格评分，不引入向量库。对历史简报提问时只读取它引用的历史快照。无匹配时返回 abstained=true。该版本只返回原文片段，不生成复杂问题的综合答案。

SSE 是自定义协议 `retrieval / citation / done`，不是 Vercel AI SDK UI 协议。两个前端都使用匹配的解析器，不能直接将本流接入不兼容的 AI SDK 客户端。

Next.js 前端使用 React 状态、TanStack Query、同源 API rewrite；本地验收页面则用原生 JS 调相同接口。后者使基础链路不依赖 Node 构建即可测试，不是 Next.js 编译产物。

## 七、参考与版本核对

以下是设计时查阅的官方资料；具体实现以上述代码及验证范围为准，而不是以框架文档替代项目验收：

- Next.js installation: https://nextjs.org/docs/app/getting-started/installation
- Next.js package metadata: https://registry.npmjs.org/next/latest
- React package metadata: https://registry.npmjs.org/react/latest
- Tailwind package metadata: https://registry.npmjs.org/tailwindcss/latest
- FastAPI Docker: https://fastapi.tiangolo.com/deployment/docker/
- LangGraph Graph API: https://docs.langchain.com/oss/python/langgraph/graph-api （后续候选，未集成）
- Celery task semantics: https://docs.celeryq.dev/en/stable/userguide/tasks.html （后续候选，未集成）
