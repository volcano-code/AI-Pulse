"""Bounded RSS/Atom/GitHub parsing. These adapters never crawl article pages."""
import json
from dataclasses import dataclass
from defusedxml import ElementTree as SafeET
from .textutil import clean_html
from .timeutil import iso, parse_time

@dataclass(frozen=True)
class Entry:
    title: str
    url: str
    text: str
    published_at: str | None = None
    updated_at: str | None = None


def parse_feed(payload: bytes, kind: str, limit: int = 30) -> list[Entry]:
    if len(payload) > 5_000_000:
        raise ValueError("Feed too large")
    if kind == "github":
        data = json.loads(payload)
        if not isinstance(data, list):
            raise ValueError("GitHub releases response must be an array")
        return [Entry(clean_html(x.get("name") or x.get("tag_name") or "Untitled"),
                      x.get("html_url", ""), clean_html(x.get("body") or ""),
                      iso(parse_time(x.get("published_at"))), None)
                for x in data[:limit] if not x.get("draft")]
    root = SafeET.fromstring(payload, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    local = lambda e: e.tag.rsplit("}", 1)[-1]
    def value(element, names):
        for child in element:
            if local(child) in names:
                return "".join(child.itertext()).strip()
        return ""
    entries = []
    for node in root.iter():
        if local(node) not in {"item", "entry"}:
            continue
        title = clean_html(value(node, {"title"}))
        text = clean_html(value(node, {"encoded", "content"}) or value(node, {"description", "summary"}))
        if local(node) == "entry":
            links = [x for x in node if local(x) == "link" and x.attrib.get("rel", "alternate") == "alternate"]
            url = links[0].attrib.get("href", "") if links else ""
        else:
            url = value(node, {"link"})
        published = value(node, {"published", "pubDate", "date"})
        updated = value(node, {"updated"})
        if title and url:
            entries.append(Entry(title, url, text, iso(parse_time(published)), iso(parse_time(updated))))
        if len(entries) >= limit:
            break
    return entries
