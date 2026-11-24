from __future__ import annotations

import hashlib
import json
import random
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from .core import now, parse_time, stamp


class FetchError(Exception):
    pass


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(req.full_url)[:2] != urlsplit(newurl)[:2]:
            raise FetchError("官方接口返回跨站重定向")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HttpClient:
    def __init__(self, db, *, timeout=15, attempts=3, sleep=time.sleep):
        self.db, self.timeout, self.attempts, self.sleep = db, timeout, attempts, sleep
        self.deadline = time.monotonic() + 600
        self.host_times = {}
        self.official_opener = urllib.request.build_opener(SameOriginRedirect())

    def get(self, url, *, headers=None, cache=True, official=False):
        host = urlsplit(url).netloc
        cooldown_key = "cooldown:" + host
        cooldown = parse_time(self.db.get(cooldown_key))
        if cooldown and cooldown > now():
            raise FetchError("渠道处于服务端限流冷却期，下个采集周期再检查")
        cache_url = "official:" + url if official else url
        key = hashlib.sha256(cache_url.encode()).hexdigest()
        prior = self.db.cache_get(key) if cache else None
        request_headers = {"User-Agent": "news-fetch/0.1 (+https://github.com/AProgramerWithoutGlasses/news-fetch)", "Accept": "application/json, application/rss+xml, application/atom+xml, text/xml;q=0.9"}
        request_headers.update(headers or {})
        if prior:
            if prior["etag"]:
                request_headers["If-None-Match"] = prior["etag"]
            if prior["modified"]:
                request_headers["If-Modified-Since"] = prior["modified"]
        for attempt in range(self.attempts):
            if time.monotonic() >= self.deadline:
                raise FetchError("本轮采集已达到 10 分钟时限，延至下一轮")
            spacing = 0.5 - (time.monotonic() - self.host_times.get(host, 0))
            if spacing > 0:
                self.sleep(spacing)
            self.host_times[host] = time.monotonic()
            delay = 2 ** attempt + random.random()
            try:
                req = urllib.request.Request(url, headers=request_headers)
                open_url = self.official_opener.open if official else urllib.request.urlopen
                with open_url(req, timeout=self.timeout) as response:
                    body = response.read(4_000_001)
                    if len(body) > 4_000_000:
                        raise FetchError("响应超过 4 MB 上限")
                    if cache:
                        self.db.cache_put(key, body, response.headers.get("ETag", ""), response.headers.get("Last-Modified", ""))
                    return body
            except urllib.error.HTTPError as exc:
                if exc.code == 304 and prior:
                    return prior["body"]
                if exc.headers.get("cf-mitigated") == "challenge":
                    raise FetchError(f"HTTP {exc.code} Cloudflare 浏览器验证") from None
                if exc.code not in (429, 500, 502, 503, 504):
                    raise FetchError(f"HTTP {exc.code}，需要检查访问权限或渠道状态") from None
                retry = exc.headers.get("Retry-After", "")
                try:
                    delay = max(delay, float(retry))
                except ValueError:
                    try:
                        delay = max(delay, (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds())
                    except (TypeError, ValueError):
                        pass
                # 不为了完成当前任务提前重试长限流；让下个采集周期再处理。
                if delay > 30:
                    self.db.set(cooldown_key, stamp(now() + timedelta(seconds=delay)))
                    raise FetchError(f"HTTP {exc.code}，服务端要求延后重试（{int(delay)} 秒）") from None
                error = f"HTTP {exc.code}，重试后仍不可用"
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                reason = str(getattr(exc, "reason", exc))
                # 不回显请求头、URL 查询参数或任何密钥。
                error = "网络或 TLS 连接失败：" + reason[:160]
                if "CERTIFICATE_VERIFY_FAILED" in reason:
                    raise FetchError("TLS 证书校验失败，已拒绝不安全连接") from None
            if attempt + 1 < self.attempts:
                self.sleep(delay)
        raise FetchError(error)

    def json(self, url, **kwargs):
        try:
            return json.loads(self.get(url, **kwargs))
        except (ValueError, UnicodeDecodeError):
            raise FetchError("响应不是有效 JSON") from None
