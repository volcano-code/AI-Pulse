"""Explicitly synthetic test corpus. Never imported by live ingestion."""
from datetime import timedelta
from .feeds import Entry
from .models import Source
from .ingestion import ingest_entries
from .events import sync_events
from .timeutil import iso, utcnow

SAMPLES = [
 ("langgraph", "【测试样本】Agent 工作流：检查点与可恢复任务", "这是一条合成测试资讯，不是真实发布公告。示例 Agent 工作流在采集、证据匹配、保存三个阶段记录状态。当前版本只能记录任务中断，不能自动恢复未完成的模型调用。此样本用于检查证据抽屉是否展示真实存储的原文。"),
 ("huggingface", "【测试样本】LLM 摘要需要区分引用匹配与事实核验", "这是一条合成测试资讯。LLM 输出包含原文引句，并不意味着摘要中的每个结论都已得到验证。系统先检查引句是否存在于快照，再将模型生成内容标记为待人工复核。原文摘录模式不调用模型。"),
 ("arxiv", "【测试样本】多语言检索：中文分词与英文关键词", "这是用于检索测试的合成论文摘要。检索链路提取英文单词与中文双字片段，使用本地词项评分寻找候选。该版本未使用 Embedding、向量数据库或 Reranker，不能宣称已完成混合 RAG。"),
 ("huggingface", "【测试样本】多模态资料阅读的边界", "这是合成测试样本，不对应任何真实产品。图表理解需要读取图表本身，不能仅依赖标题猜测实验结果。本轮仅保存来源提供的文字内容，尚未实现 PDF 图表解析。"),
 ("langgraph", "【测试样本】开源发布记录与版本对比", "这是合成测试发布记录。相同 URL 的内容变化会创建新的不可变快照。历史简报保留旧快照，避免来源更新后改变旧版简报的证据。版本号在此仅作为测试文本，不代表真实产品版本。"),
 ("arxiv", "【测试样本】Agent 预算：限制页面数量与执行轮次", "这是一条合成资讯。示例系统应限制单次运行的来源数、读取字节与模型调用次数。达到预算上限时应停止并记录原因。当前本地版本不提供自主联网研究或未经授权的外部写入工具。"),
]

def seed_demo(session_factory, settings):
    if settings.data_mode != "replay":
        raise ValueError("Demo seeding requires DATA_MODE=replay and a separate database")
    now = utcnow()
    totals = []
    with session_factory.begin() as db:
        for i, (source_id, title, text) in enumerate(SAMPLES):
            source = db.get(Source, source_id)
            entry = Entry(title, f"https://example.com/ai-pulse-test/{i + 1}", text,
                          iso(now - timedelta(hours=i + 1)))
            totals.append(ingest_entries(db, source, [entry], "replay"))
        sync_events(db, "replay")
    return totals
