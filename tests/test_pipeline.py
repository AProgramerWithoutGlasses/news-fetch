import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from news_fetch import cli
from news_fetch.core import Item, UTC, canonical_url, latest_cutoff, now, parse_time, stamp
from news_fetch.db import Database
from news_fetch.http import FetchError, HttpClient
from news_fetch.report import build_report, deduplicate, webpage, write_report
from news_fetch.sources import parse_feed, x_status


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "db.sqlite3")
        self.addCleanup(self.db.conn.close)

    def item(self, ident="1", published=None, source="X", title="Claude protocol update"):
        return Item(source, ident, title, "https://example.com/article?utm_source=test", stamp(published or now()), "Protocol details", "https://example.com/discussion")

    def test_cutoff_cst_before_and_after_eight(self):
        self.assertEqual(stamp(latest_cutoff(datetime(2026, 11, 19, 0, 0, tzinfo=UTC))), "2026-11-19T00:00:00+00:00")
        self.assertEqual(stamp(latest_cutoff(datetime(2026, 11, 18, 23, 59, tzinfo=UTC))), "2026-11-18T00:00:00+00:00")

    def test_window_boundaries_and_rerun_upsert(self):
        end = now().replace(microsecond=0)
        start = end - timedelta(hours=24)
        items = [self.item("start", start), self.item("end", end), self.item("old", start - timedelta(seconds=1))]
        self.db.save(items)
        self.db.save(items)
        self.assertEqual([r["source_id"] for r in self.db.items(start, end)], ["start"])

    def test_invalid_missing_and_future_dates_rejected(self):
        item = self.item()
        item.published_at = ""
        self.assertEqual(self.db.save([item, self.item("future", now() + timedelta(days=1))]), 0)
        self.assertIsNone(parse_time("2026-11-19T08:00:00"))

    def test_cross_source_canonical_dedup_keeps_references(self):
        self.db.save([self.item(source="X"), self.item(source="V2EX")])
        items = deduplicate(self.db.items(now() - timedelta(hours=1), now() + timedelta(seconds=1)))
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0]["references"]), 2)
        self.assertEqual(items[0]["url"], "https://example.com/article")
        self.assertEqual(canonical_url("javascript:alert(1)"), "")

    def test_atom_does_not_treat_updated_as_published(self):
        body = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Claude</title><link href="https://example.com/a"/><updated>2026-11-19T00:00:00Z</updated></entry></feed>'
        self.assertEqual(parse_feed(body, "V2EX")[0].published_at, "")
        with self.assertRaises(FetchError):
            parse_feed(b"<html><title>captcha</title></html>", "V2EX")

    def test_x_status_timestamp_and_domain_validation(self):
        moment = datetime(2026, 11, 18, tzinfo=UTC)
        ident = (int(moment.timestamp() * 1000) - 1288834974657) << 22
        result = x_status(f"https://twitter.com/openai/status/{ident}?utm_source=x")
        self.assertEqual(result[1], moment)
        self.assertIsNone(x_status(f"https://evil.example/openai/status/{ident}"))

    def test_source_failure_does_not_abort_other_sources(self):
        def failure(*args):
            raise FetchError("HTTP 403")
        def success(*args):
            return [self.item()], "ok", "test"
        with patch.dict(cli.COLLECTORS, {"X": failure, "V2EX": success}, clear=True):
            statuses = cli.collect(self.db, self.root)
        self.assertEqual([s["status"] for s in statuses], ["failed", "ok"])
        self.assertTrue((self.root / "health.json").exists())

    def test_429_persists_retry_after_and_skips_next_request(self):
        error = urllib.error.HTTPError("https://example.com", 429, "rate limit", {"Retry-After": "7200"}, io.BytesIO())
        client = HttpClient(self.db, sleep=lambda _: None)
        with patch("urllib.request.urlopen", side_effect=error) as request:
            with self.assertRaises(FetchError):
                client.get("https://example.com/feed")
            with self.assertRaises(FetchError):
                client.get("https://example.com/another")
        self.assertEqual(request.call_count, 1)

    def test_conditional_304_uses_cached_body(self):
        import hashlib
        url = "https://example.com/feed"
        self.db.cache_put(hashlib.sha256(url.encode()).hexdigest(), b"cached", "etag", "")
        error = urllib.error.HTTPError(url, 304, "unchanged", {}, io.BytesIO())
        with patch("urllib.request.urlopen", side_effect=error):
            self.assertEqual(HttpClient(self.db).get(url), b"cached")

    def test_permission_error_does_not_retry(self):
        error = urllib.error.HTTPError("https://example.com", 403, "forbidden", {}, io.BytesIO())
        with patch("urllib.request.urlopen", side_effect=error) as request:
            with self.assertRaises(FetchError):
                HttpClient(self.db, sleep=lambda _: None).get("https://example.com/feed")
        self.assertEqual(request.call_count, 1)

    def test_503_retries_bounded_times(self):
        error = urllib.error.HTTPError("https://example.com", 503, "unavailable", {}, io.BytesIO())
        with patch("urllib.request.urlopen", side_effect=error) as request:
            with self.assertRaises(FetchError):
                HttpClient(self.db, sleep=lambda _: None).get("https://example.com/feed")
        self.assertEqual(request.call_count, 3)

    def test_llm_failure_keeps_original_items(self):
        from news_fetch.report import enrich
        items = [{"id": "a", "title": "Claude update", "text": "Source text"}]
        with patch.dict("os.environ", {"NEWS_LLM_BASE_URL": "https://example.com/v1", "NEWS_LLM_API_KEY": "test-secret", "NEWS_LLM_MODEL": "test"}):
            with patch("urllib.request.urlopen", side_effect=TimeoutError):
                status = enrich(items)
        self.assertIn("降级", status)
        self.assertEqual(items[0]["title"], "Claude update")
        self.assertNotIn("test-secret", status)

    def test_new_install_reports_collection_gap(self):
        self.db.record_fetch("X", now(), "limited", 0, "public search")
        report = build_report(self.db, now(), use_llm=False)
        self.assertFalse(next(c for c in report["coverage"] if c["source"] == "X")["continuous"])

    def test_all_failed_still_produces_report_with_status(self):
        self.db.record_fetch("X", now(), "failed", 0, "HTTP 403")
        report = build_report(self.db, now(), use_llm=False)
        path = write_report(report, self.root)
        self.assertTrue(path.exists())
        self.assertEqual(report["items"], [])
        self.assertEqual(next(c for c in report["coverage"] if c["source"] == "X")["status"], "failed")

    def test_html_escapes_untrusted_source(self):
        self.db.save([self.item(title='Claude <script>alert(1)</script>')])
        report = build_report(self.db, now() + timedelta(seconds=1), use_llm=False)
        output = webpage(report)
        self.assertNotIn("<script>", output)
        self.assertIn("&lt;script&gt;", output)

    def test_tick_idempotent_and_failed_report_retries(self):
        at = now()
        self.db.set("last_collect", stamp(at))
        with patch.object(cli, "collect"), patch.object(cli, "write_report", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                cli.tick(self.db, self.root, at=at, use_llm=False)
        self.assertIsNone(self.db.get("last_daily_report"))
        with patch.object(cli, "collect"), patch.object(cli, "write_report", return_value=self.root / "report.md") as writer:
            cli.tick(self.db, self.root, at=at, use_llm=False)
            cli.tick(self.db, self.root, at=at, use_llm=False)
            self.assertEqual(writer.call_count, 1)

    def test_lease_blocks_concurrent_run(self):
        other = Database(self.root / "db.sqlite3")
        self.addCleanup(other.conn.close)
        with self.db.lease("pipeline"):
            with self.assertRaises(RuntimeError):
                with other.lease("pipeline"):
                    pass


if __name__ == "__main__":
    unittest.main()
