from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from .core import AI_RE, canonical_url, now, parse_time, stamp


class Database:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=20)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA busy_timeout=20000;
        CREATE TABLE IF NOT EXISTS items (
          source TEXT, source_id TEXT, title TEXT, url TEXT, published_at TEXT,
          text TEXT, discussion_url TEXT, provenance TEXT, first_seen TEXT, last_seen TEXT,
          PRIMARY KEY(source, source_id));
        CREATE INDEX IF NOT EXISTS items_time ON items(published_at);
        CREATE TABLE IF NOT EXISTS fetches (
          id INTEGER PRIMARY KEY, source TEXT, started_at TEXT, finished_at TEXT,
          status TEXT, count INTEGER, detail TEXT);
        CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, body BLOB, etag TEXT, modified TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS leases (name TEXT PRIMARY KEY, owner TEXT, expires TEXT);
        """)
        if "updated_at" not in [row[1] for row in self.conn.execute("PRAGMA table_info(cache)")]:
            with self.conn:
                self.conn.execute("ALTER TABLE cache ADD COLUMN updated_at TEXT")

        if "official_only" not in [row[1] for row in self.conn.execute("PRAGMA table_info(fetches)")]:
            with self.conn:
                self.conn.execute("ALTER TABLE fetches ADD COLUMN official_only INTEGER NOT NULL DEFAULT 0")

    def cache_get(self, key):
        return self.conn.execute("SELECT * FROM cache WHERE key=?", (key,)).fetchone()

    def cache_put(self, key, body, etag, modified):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)", (key, body, etag, modified, stamp(now())))

    def save(self, items):
        count = 0
        seen = stamp(now())
        with self.conn:
            for item in items:
                published = parse_time(item.published_at)
                url = canonical_url(item.url)
                if not published or published > now() + timedelta(minutes=5) or not url or not AI_RE.search(item.title + " " + item.text):
                    continue
                self.conn.execute("""INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(source,source_id) DO UPDATE SET title=excluded.title,text=excluded.text,
                  url=excluded.url,last_seen=excluded.last_seen,published_at=excluded.published_at,
                  discussion_url=excluded.discussion_url,provenance=excluded.provenance""",
                  (item.source, item.source_id, item.title, url, stamp(published), item.text[:5000],
                   canonical_url(item.discussion_url), item.provenance, seen, seen))
                count += 1
        return count

    def record_fetch(self, source, started, status, count, detail):
        with self.conn:
            self.conn.execute("INSERT INTO fetches(source,started_at,finished_at,status,count,detail,official_only) VALUES (?,?,?,?,?,?,?)",
                              (source, stamp(started), stamp(now()), status, count, detail, source in ("X", "Linux.do")))

    def items(self, start, end):
        return [dict(row) for row in self.conn.execute("""SELECT * FROM items WHERE published_at>=? AND published_at<?
          AND (source != 'X' OR provenance='official-api')
          AND (source != 'Linux.do' OR provenance IN ('direct', 'firecrawl-rss'))
          ORDER BY published_at DESC""", (stamp(start), stamp(end)))]

    def fetches(self, start, end):
        return [dict(row) for row in self.conn.execute("SELECT * FROM fetches WHERE finished_at>=? AND started_at<=? AND (source NOT IN ('Linux.do','X') OR official_only=1) ORDER BY id", (stamp(start), stamp(end)))]

    def get(self, key):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key, value):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    @contextmanager
    def lease(self, name):
        owner = uuid.uuid4().hex
        with self.conn:
            self.conn.execute("DELETE FROM leases WHERE expires<?", (stamp(now()),))
            try:
                self.conn.execute("INSERT INTO leases VALUES (?,?,?)", (name, owner, stamp(now() + timedelta(minutes=30))))
            except sqlite3.IntegrityError:
                raise RuntimeError("已有采集或日报任务运行中") from None
        try:
            yield
        finally:
            with self.conn:
                self.conn.execute("DELETE FROM leases WHERE name=? AND owner=?", (name, owner))

    def prune(self):
        cutoff = stamp(now() - timedelta(days=35))
        with self.conn:
            self.conn.execute("DELETE FROM items WHERE published_at<?", (cutoff,))
            self.conn.execute("DELETE FROM fetches WHERE finished_at<?", (cutoff,))
            self.conn.execute("DELETE FROM cache WHERE updated_at IS NULL OR updated_at<?", (stamp(now() - timedelta(days=2)),))
