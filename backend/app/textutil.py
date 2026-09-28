import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        if tag in {"p", "div", "br", "li", "h1", "h2"}:
            self.parts.append(" ")
    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)
        self.parts.append(" ")
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

def clean_html(value: str) -> str:
    parser = PlainText()
    parser.feed(value[:200_000])
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()[:40_000]

def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Only public article http(s) links without credentials are accepted")
    if any(ord(c) < 32 for c in url) or len(url) > 3000:
        raise ValueError("Invalid URL")
    host = parts.hostname.encode("idna").decode().lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    if port and (parts.scheme, port) not in {("http", 80), ("https", 443)}:
        host += f":{port}"
    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
              if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", urlencode(params), ""))

def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def topic_for(text: str, source: str = "") -> str:
    t = text.casefold()
    for topic, keys in [
        ("Agent", ["agent", "langgraph", "工具调用", "智能体", "mcp"]),
        ("多模态", ["multimodal", "vision", "多模态", "图像"]),
        ("LLM", ["llm", "language model", "大模型", "语言模型", "reasoning"]),
    ]:
        if any(k in t for k in keys):
            return topic
    return "论文" if source == "arxiv" else "开源" if source == "langgraph" else "其他"
