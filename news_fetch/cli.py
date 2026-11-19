from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import time
from datetime import timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core import latest_cutoff, load_env, now, parse_time, stamp
from .db import Database
from .http import HttpClient
from .report import atomic_write, build_report, write_report
from .sources import COLLECTORS

LOG = logging.getLogger("news-fetch")


def collect(db, report_dir, *, start=None):
    end = now()
    start = start or end - timedelta(hours=24)
    client = HttpClient(db)
    statuses = []
    for source, collector in COLLECTORS.items():
        started = now()
        try:
            items, status, detail = collector(client, start, end)
            count = db.save(items)
        except Exception as exc:
            from .http import FetchError
            status, count = "failed", 0
            detail = str(exc) if isinstance(exc, FetchError) else f"采集器异常（{type(exc).__name__}），请检查接口结构"
        db.record_fetch(source, started, status, count, detail)
        statuses.append({"source": source, "status": status, "accepted": count, "detail": detail})
        LOG.info("%s %s accepted=%s %s", source, status, count, detail)
    db.set("last_collect", stamp(now()))
    db.prune()
    health = {"last_collect": db.get("last_collect"), "last_daily_report": db.get("last_daily_report"),
              "degraded": any(s["status"] != "ok" for s in statuses), "sources": statuses}
    atomic_write(Path(report_dir) / "health.json", json.dumps(health, ensure_ascii=False, indent=2))
    return statuses


def tick(db, report_dir, *, at=None, use_llm=True):
    at = at or now()
    cutoff = latest_cutoff(at)
    due = db.get("last_daily_report") != stamp(cutoff)
    last = parse_time(db.get("last_collect"))
    interval = max(5, int(os.getenv("NEWS_POLL_MINUTES", "20")))
    if due or not last or at - last >= timedelta(minutes=interval):
        collect(db, report_dir, start=cutoff - timedelta(hours=24) if due else None)
    if due:
        report = build_report(db, cutoff, use_llm=use_llm)
        path = write_report(report, report_dir)
        # 只有完整写出日报后才记成功，崩溃后的下一轮可重试。
        db.set("last_daily_report", stamp(cutoff))
        db.set("last_report_path", str(path))
        health_path = Path(report_dir) / "health.json"
        if health_path.exists():
            health = json.loads(health_path.read_text(encoding="utf-8"))
            health["last_daily_report"] = stamp(cutoff)
            atomic_write(health_path, json.dumps(health, ensure_ascii=False, indent=2))
        LOG.info("Daily report saved: %s", path)


class ReportHandler(SimpleHTTPRequestHandler):
    def list_directory(self, path):
        self.send_error(403, "Directory listing disabled")
        return None

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main():
    load_env()
    parser = argparse.ArgumentParser(description="AI 新闻与逆向动态采集、日报与定时运行")
    parser.add_argument("command", choices=("collect", "report", "run", "tick", "daemon", "serve", "status"))
    parser.add_argument("--now", action="store_true", help="按当前时刻的前 24h 生成预览；默认按最近北京时间 08:00")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    data_dir = Path(os.getenv("NEWS_DATA_DIR", "data")).resolve()
    report_dir = Path(os.getenv("NEWS_REPORT_DIR", "reports")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler
    log_name = "web.log" if args.command == "serve" else "news-fetch.log"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.StreamHandler(), RotatingFileHandler(data_dir / log_name, maxBytes=2_000_000, backupCount=3, encoding="utf-8")])
    if args.command == "serve":
        server = ThreadingHTTPServer((args.host, args.port), functools.partial(ReportHandler, directory=str(report_dir)))
        LOG.info("Serving reports on http://%s:%s", args.host, args.port)
        server.serve_forever()
        return
    db = Database(data_dir / "news.sqlite3")
    if args.command == "status":
        print(json.dumps({"last_collect": db.get("last_collect"), "last_daily_report": db.get("last_daily_report"), "report": db.get("last_report_path")}, ensure_ascii=False, indent=2))
        return
    if args.command == "daemon":
        while True:
            try:
                with db.lease("pipeline"):
                    tick(db, report_dir, use_llm=not args.no_llm)
            except Exception as exc:
                LOG.error("Scheduled tick failed (%s); will retry", type(exc).__name__)
            time.sleep(10)
    else:
        with db.lease("pipeline"):
            if args.command == "tick":
                tick(db, report_dir, use_llm=not args.no_llm)
            else:
                if args.command in ("run", "collect"):
                    collect(db, report_dir)
                if args.command in ("run", "report"):
                    end = now() if args.now else latest_cutoff(now())
                    path = write_report(build_report(db, end, use_llm=not args.no_llm), report_dir)
                    print(path)


if __name__ == "__main__":
    main()
