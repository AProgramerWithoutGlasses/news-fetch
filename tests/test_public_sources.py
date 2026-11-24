import tempfile
import urllib.request
from datetime import timedelta
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from news_fetch.core import Item, now, stamp
from news_fetch.db import Database
from news_fetch.http import FetchError, SameOriginRedirect
from news_fetch.sources import linux_do, x


class OfficialSourcesTests(TestCase):
    def test_linux_challenge_stops_at_original_site(self):
        client = Mock()
        client.get.side_effect = client.json.side_effect = FetchError("HTTP 403 Cloudflare 浏览器验证")
        with self.assertRaisesRegex(FetchError, "Cloudflare"):
            linux_do(client, now() - timedelta(days=1), now())
        client.get.assert_called_once_with("https://linux.do/latest.rss", official=True)
        client.json.assert_called_once_with("https://linux.do/latest.json", official=True)

    def test_missing_token_does_not_search_elsewhere(self):
        client = Mock()
        with patch.dict("os.environ", {"X_BEARER_TOKEN": ""}), self.assertRaisesRegex(FetchError, "X_BEARER_TOKEN"):
            x(client, now() - timedelta(days=1), now())
        self.assertFalse(client.mock_calls)

    def test_x_official_page_then_rate_limit_keeps_verified_posts(self):
        client = Mock()
        at = now()
        client.json.side_effect = [
            {"data": [{"id": "123", "created_at": stamp(at), "text": "Claude news", "author_id": "5"}],
             "includes": {"users": [{"id": "5", "username": "ClaudeDevs"}]},
             "meta": {"next_token": "more", "result_count": 1}},
            FetchError("HTTP 429")]
        with patch.dict("os.environ", {"X_BEARER_TOKEN": "test"}):
            items, status, _ = x(client, at - timedelta(days=1), at + timedelta(seconds=1))
        self.assertEqual((status, items[0].url, items[0].provenance), ("limited", "https://x.com/ClaudeDevs/status/123", "official-api"))
        for call in client.json.call_args_list:
            self.assertTrue(call.args[0].startswith("https://api.x.com/2/tweets/search/recent?"))
            self.assertTrue(call.kwargs["official"])

    def test_external_redirect_rejected_before_request(self):
        req = urllib.request.Request("https://api.x.com/2/tweets/search/recent")
        with self.assertRaises(FetchError):
            SameOriginRedirect().redirect_request(req, None, 302, "", {}, "https://example.org/")

    def test_legacy_indirect_rows_excluded_until_fetched_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "db")
            try:
                at = now().replace(microsecond=0)
                old = stamp(at - timedelta(hours=2))
                original = stamp(at - timedelta(hours=1))
                db.save([Item("X", "123", "Claude news", "https://x.com/A/status/123", old, provenance="x-oembed"),
                         Item("Linux.do", "channel:42", "Claude news", "https://linux.do/t/topic/42", old, provenance="telegram-relay")])
                with db.conn:
                    db.conn.execute("INSERT INTO fetches(source,started_at,finished_at,status,count,detail) VALUES (?,?,?,?,?,?)",
                                    ("X", old, old, "limited", 1, "legacy"))
                self.assertFalse(db.items(at - timedelta(days=1), at))
                self.assertFalse(db.fetches(at - timedelta(days=1), at))
                db.save([Item("X", "123", "Claude news", "https://x.com/A/status/123", original, provenance="official-api")])
                items = db.items(at - timedelta(days=1), at)
                self.assertEqual([(i["source"], i["published_at"]) for i in items], [("X", original)])
            finally:
                db.conn.close()
