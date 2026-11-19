from __future__ import annotations

import html
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

CST = timezone(timedelta(hours=8), "Asia/Shanghai")
UTC = timezone.utc
SOURCES = ("Hacker News", "V2EX", "Linux.do", "X")


def now() -> datetime:
    return datetime.now(UTC)


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def parse_time(value: str | int | float | None) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, UTC)
        from email.utils import parsedate_to_datetime
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            result = parsedate_to_datetime(value)
        # 没有时区的日期不能作为已核实的发布时间。
        return result.astimezone(UTC) if result.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def clean(value: str) -> str:
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", value, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def canonical_url(url: str) -> str:
    parts = urlsplit(html.unescape(url.strip()))
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username:
        return ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
    host = parts.netloc.lower()
    if host in ("twitter.com", "www.twitter.com", "www.x.com"):
        host = "x.com"
    path = quote(parts.path.rstrip("/") or "/", safe="/%:@!$&'()*+,;=-._~")
    return urlunsplit((parts.scheme, host, path, urlencode(sorted(query)), ""))


AI_RE = re.compile(r"(?i)(?<![a-z])(?:ai|llm|gpt[\w.-]*|chatgpt|claude|gemini|openai|anthropic|deepseek|qwen|llama|mistral|codex|copilot|cursor|mcp|ollama)(?![a-z])|人工智能|大模型|语言模型|机器学习|通义|智谱|千问")
REVERSE_RE = re.compile(r"(?i)reverse.?engineer|reverse.?proxy|unofficial|oauth|rate.?limit|protocol|api.?compatib|claude.?code|cli.?proxy|sub2api|new.?api|逆向|反代|协议|风控|封号|第三方客户端|兼容接口|额度|限流|令牌|抓包")


@dataclass
class Item:
    source: str
    source_id: str
    title: str
    url: str
    published_at: str
    text: str = ""
    discussion_url: str = ""
    provenance: str = "direct"

    def to_dict(self):
        return asdict(self)


def classify(item: dict) -> tuple[str, list[str], int]:
    content = item["title"] + " " + item.get("text", "")
    tags = []
    for tag, pattern in (("GPT/OpenAI", r"gpt|openai|codex"), ("Claude", r"claude|anthropic"), ("Gemini", r"gemini|google.?ai")):
        if re.search(pattern, content, re.I):
            tags.append(tag)
    category = "reverse" if REVERSE_RE.search(content) else "news"
    return category, tags or ["AI"], len(tags) * 10 + (4 if category == "reverse" else 0)


def load_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def latest_cutoff(at: datetime) -> datetime:
    local = at.astimezone(CST)
    cutoff = local.replace(hour=8, minute=0, second=0, microsecond=0)
    return (cutoff if local >= cutoff else cutoff - timedelta(days=1)).astimezone(UTC)
