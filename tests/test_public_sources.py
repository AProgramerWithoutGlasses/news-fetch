from datetime import timedelta
from unittest import TestCase
from unittest.mock import Mock

from news_fetch.core import now, stamp
from news_fetch.sources import linux_channel, x_public


class PublicSourcesTests(TestCase):
    def test_relay_uses_channel_date_and_original_topic(self):
        end = now()
        date = stamp(end - timedelta(hours=2))
        client = Mock()
        client.get.return_value = (f'<div data-post="linux_do_channel/5"><div class="tgme_widget_message_text js-message_text"><b>A</b> 在 <a href="https://linux.do/t/topic/123/1">Claude 更新</a> 中发帖<br/><pre>正文</pre></div><time datetime="{date}"></time></div>').encode()
        items, status, _ = linux_channel(client, end - timedelta(days=1), end, "HTTP 403")
        self.assertEqual(status, "limited")
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0].url, items[0].discussion_url, items[0].provenance, items[0].published_at),
                         ("https://linux.do/t/topic/123", "https://t.me/linux_do_channel/5", "telegram-relay", date))
        self.assertEqual(linux_channel(client, end - timedelta(hours=1), end, "HTTP 403")[1], "no_hits")

    def test_only_verified_in_window_x_post_supplies_content(self):
        end = now()
        def post(hours):
            ts = (end - timedelta(hours=hours)).timestamp()
            return "https://x.com/ClaudeDevs/status/" + str((int(ts * 1000) - 1288834974657) << 22)
        live, old = post(1), post(48)
        client = Mock()
        client.json.side_effect = [{"items": [{"body": "invented " + live + " " + old}]},
                                   {"url": live, "html": "<blockquote><p>Claude release</p></blockquote>"}]
        items, status, _ = x_public(client, end - timedelta(days=1), end)
        self.assertEqual(status, "limited")
        self.assertEqual([(i.url, i.text) for i in items], [(live, "Claude release")])
        self.assertEqual(client.json.call_count, 2)
