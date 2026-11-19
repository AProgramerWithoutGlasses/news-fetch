from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

from .core import Item, UTC, canonical_url, clean, now, parse_time, stamp
from .http import FetchError


def parse_feed(body: bytes, source: str) -> list[Item]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise FetchError("响应不是有效 RSS/Atom，可能返回了验证页面") from None
    if root.tag.split("}")[-1] not in ("rss", "feed", "RDF"):
        raise FetchError("响应不是 RSS/Atom")
    items = []
    for entry in root.iter():
        if entry.tag.split("}")[-1] not in ("item", "entry"):
            continue
        fields, link = {}, ""
        for child in entry:
            key = child.tag.split("}")[-1]
            fields[key] = child.text or ""
            if key == "link" and child.attrib.get("rel", "alternate") == "alternate":
                link = child.attrib.get("href") or child.text or ""
        published = parse_time(fields.get("published") or fields.get("pubDate") or fields.get("date"))
        # 不使用 Atom updated 或采集时间冒充首次发布时间。
        items.append(Item(source, fields.get("guid") or fields.get("id") or link,
                          clean(fields.get("title", "")), link,
                          stamp(published) if published else "",
                          clean(fields.get("encoded") or fields.get("content") or fields.get("description") or fields.get("summary", "")), link))
    return items


def hacker_news(client, start, end):
    items, errors, capped = [], [], False
    for query in ("GPT", "Claude", "Gemini", "AI", "LLM", "OpenAI", "Anthropic", "Codex"):
        for page in range(3):
            params = urlencode({"query": query, "tags": "story", "numericFilters": f"created_at_i>={int(start.timestamp())},created_at_i<{int(end.timestamp())}", "hitsPerPage": 100, "page": page})
            try:
                data = client.json("https://hn.algolia.com/api/v1/search_by_date?" + params)
                hits = data["hits"]
                for hit in hits:
                    published = parse_time(hit.get("created_at"))
                    discussion = "https://news.ycombinator.com/item?id=" + str(hit["objectID"])
                    items.append(Item("Hacker News", str(hit["objectID"]), clean(hit.get("title") or ""), hit.get("url") or discussion,
                                      stamp(published) if published else "", clean(hit.get("story_text") or ""), discussion, "algolia"))
                if page + 1 >= data.get("nbPages", 1):
                    break
                if page == 2:
                    capped = True
            except (FetchError, KeyError, TypeError) as exc:
                errors.append(str(exc) if isinstance(exc, FetchError) else "搜索响应结构异常")
                break
    # 搜索索引整体失败时，官方 API 作为有限范围的后备来源。
    if not items and errors:
        try:
            ids = client.json("https://hacker-news.firebaseio.com/v0/newstories.json")[:60]
            for ident in ids:
                hit = client.json(f"https://hacker-news.firebaseio.com/v0/item/{int(ident)}.json")
                if not hit or hit.get("deleted") or hit.get("dead"):
                    continue
                published = parse_time(hit.get("time"))
                discussion = f"https://news.ycombinator.com/item?id={int(ident)}"
                items.append(Item("Hacker News", str(ident), clean(hit.get("title", "")), hit.get("url") or discussion,
                                  stamp(published) if published else "", clean(hit.get("text", "")), discussion, "official-api"))
            return items, "limited", "Algolia 不可用；官方 API 后备仅覆盖最近 60 条帖子"
        except (FetchError, TypeError, ValueError):
            raise FetchError("搜索索引与官方 API 均不可用") from None
    detail = "按发布时间查询 HN Algolia 索引；关键词检索不保证全量"
    if errors:
        detail += "；部分查询失败：" + errors[0]
    if capped:
        detail += "；达到分页上限"
    return items, "limited" if errors or capped else "ok", detail


def v2ex(client, start, end):
    errors = []
    try:
        data = client.json("https://www.v2ex.com/api/topics/latest.json")
        items = []
        for hit in data:
            published = parse_time(hit.get("created"))
            items.append(Item("V2EX", str(hit["id"]), clean(hit["title"]), hit["url"], stamp(published) if published else "", clean(hit.get("content", "")), hit["url"]))
        return items, "limited", "公开 latest API 仅含最近帖子；持续轮询仍可能遗漏高峰期间帖子"
    except (FetchError, KeyError, TypeError) as exc:
        errors.append(str(exc) if isinstance(exc, FetchError) else "API 响应结构异常")
    for url in ("https://www.v2ex.com/index.xml", "https://v2ex.com/index.xml"):
        try:
            return parse_feed(client.get(url), "V2EX"), "limited", "RSS 后备来源仅含最近帖子；API 不可用"
        except FetchError as exc:
            errors.append(str(exc))
    raise FetchError("API/RSS 均不可用：" + errors[0])


def linux_do(client, start, end):
    try:
        return parse_feed(client.get("https://linux.do/latest.rss"), "Linux.do"), "limited", "公开 RSS 仅含最近主题，不含私有版块；持续轮询仍可能漏帖"
    except FetchError as rss_error:
        try:
            data = client.json("https://linux.do/latest.json")
            items = []
            for hit in data["topic_list"]["topics"]:
                published = parse_time(hit.get("created_at"))
                link = f"https://linux.do/t/topic/{int(hit['id'])}"
                items.append(Item("Linux.do", str(hit["id"]), clean(hit["title"]), link, stamp(published) if published else "", "", link))
            return items, "limited", "公开 latest.json 后备仅含最近主题；RSS 不可用"
        except (FetchError, KeyError, TypeError):
            raise FetchError("RSS/JSON 均不可用：" + str(rss_error)) from None


def x_status(url):
    # Bing 有时使用新闻跳转地址，但只有明确的 x/twitter 原帖链接才能入库。
    parts = urlsplit(url)
    if parts.hostname and parts.hostname.endswith("bing.com"):
        url = parse_qs(parts.query).get("url", [url])[0]
    url = canonical_url(url)
    match = re.fullmatch(r"https?://x\.com/(?:[A-Za-z0-9_]+|i/web)/status/(\d+)(?:\?.*)?", url)
    if not match:
        return None
    ident = match[1]
    published = datetime.fromtimestamp(((int(ident) >> 22) + 1288834974657) / 1000, UTC)
    if not datetime(2010, 11, 4, tzinfo=UTC) <= published <= now() + timedelta(minutes=5):
        return None
    return ident, published, url.split("?")[0]


def x_public(client):
    items, failures = [], []
    for query in ("site:x.com (OpenAI OR GPT OR Claude OR Gemini)", "site:x.com (Claude OR Gemini OR GPT) (reverse OR unofficial OR API OR 逆向)"):
        url = "https://www.bing.com/search?" + urlencode({"q": query, "format": "rss"})
        try:
            for hit in parse_feed(client.get(url), "X"):
                status = x_status(hit.url)
                if status:
                    ident, published, link = status
                    items.append(Item("X", ident, hit.title, link, stamp(published), hit.text, link, "public-search"))
        except FetchError as exc:
            failures.append(str(exc))
    if len(failures) == 2:
        raise FetchError("未配置 X API；公开搜索也不可用：" + failures[0])
    return items, "limited", "未使用 X 官方 API；仅公开搜索索引线索，覆盖有限且可能滞后。原帖时间由 status ID 校验；摘录未核实，不代表事件已证实" + ("；部分搜索失败" if failures else "")


def x(client, start, end):
    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    if not token:
        return x_public(client)
    params = {"query": '(GPT OR ChatGPT OR OpenAI OR Claude OR Anthropic OR Gemini) -is:retweet', "start_time": stamp(max(start, now() - timedelta(days=6))), "end_time": stamp(min(end, now() - timedelta(seconds=15))), "max_results": 100, "tweet.fields": "created_at", "expansions": "author_id", "user.fields": "username"}
    items = []
    for page in range(3):
        try:
            data = client.json("https://api.x.com/2/tweets/search/recent?" + urlencode(params), headers={"Authorization": "Bearer " + token}, cache=False)
            if data.get("errors"):
                raise FetchError("X API 返回错误，请检查账号权限或额度")
            users = {u["id"]: u["username"] for u in data.get("includes", {}).get("users", [])}
            for hit in data.get("data", []):
                published = parse_time(hit.get("created_at"))
                text = clean(hit["text"])
                link = f"https://x.com/{users.get(hit.get('author_id'), 'i/web')}/status/{hit['id']}"
                items.append(Item("X", hit["id"], text[:180], link, stamp(published) if published else "", text, link, "official-api"))
            next_token = data.get("meta", {}).get("next_token")
            if not next_token:
                return items, "ok", "X 官方 recent search；关键词匹配，未核实帖子中的消息"
            params["next_token"] = next_token
        except FetchError as exc:
            if items:
                return items, "limited", "X API 部分分页失败：" + str(exc)
            fallback, status, detail = x_public(client)
            return fallback, status, "X API 不可用：" + str(exc) + "；" + detail
    return items, "limited", "X 官方 API 已达到 300 条分页上限，结果不完整"


COLLECTORS = {"Hacker News": hacker_news, "V2EX": v2ex, "Linux.do": linux_do, "X": x}
