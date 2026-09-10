import unittest

import httpx

from hotspot_agent.sources import DOMESTIC_SOURCES, SourceConfig, fetch_all_with_stats, fetch_feed, parse_feed


RSS_FIXTURE = b'''<?xml version="1.0"?><rss version="2.0"><channel><item><title>AI launch</title><description>Useful update</description><link>https://example.com/a</link><pubDate>Wed, 20 Aug 2026 08:00:00 GMT</pubDate></item></channel></rss>'''
ATOM_FIXTURE = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Agent update</title><summary>Human review</summary><link href="https://example.com/b"/><updated>2026-08-20T08:00:00Z</updated></entry></feed>'''
HN_FIXTURE = b'''<?xml version="1.0"?><rss version="2.0"><channel><item><title>Show HN: Useful AI workflow</title><description><![CDATA[Article URL: https://example.com/ai-workflow Comments URL: https://news.ycombinator.com/item?id=123 Points: 7 # Comments: 3]]></description><link>https://news.ycombinator.com/item?id=123</link><pubDate>Wed, 20 Aug 2026 08:00:00 GMT</pubDate></item></channel></rss>'''


class SourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SourceConfig("媒体新闻", "Fixture", "https://example.com/feed")

    def test_domestic_preset_has_three_direct_feeds(self) -> None:
        self.assertEqual([source.source_name for source in DOMESTIC_SOURCES], ["36氪", "少数派", "IT之家"])
        self.assertEqual(
            [source.url for source in DOMESTIC_SOURCES],
            ["https://36kr.com/feed", "https://sspai.com/feed", "https://www.ithome.com/rss/"],
        )

    def test_rss_and_atom_are_normalized(self) -> None:
        rss_items = parse_feed(RSS_FIXTURE, self.config)
        atom_items = parse_feed(ATOM_FIXTURE, self.config)
        self.assertEqual(rss_items[0].title, "AI launch")
        self.assertEqual(atom_items[0].url, "https://example.com/b")
        self.assertEqual(atom_items[0].summary, "Human review")

    def test_hacker_news_metadata_becomes_readable_summary(self) -> None:
        config = SourceConfig("社区趋势", "Hacker News AI", "https://hnrss.org/newest?q=AI")
        item = parse_feed(HN_FIXTURE, config)[0]
        self.assertEqual(item.url, "https://example.com/ai-workflow")
        self.assertEqual(
            item.summary,
            "A Hacker News discussion links to example.com about this story. It currently has 7 points and 3 comments.",
        )
        self.assertEqual(item.engagement_points, 7)
        self.assertEqual(item.engagement_comments, 3)
        self.assertGreater(item.heat_score, 0)
        for marker in ("Article URL:", "Comments URL:", "Points:", "# Comments:"):
            self.assertNotIn(marker, item.summary)

    def test_fetch_retries_then_succeeds(self) -> None:
        calls = []

        def request(_url, _timeout, _headers):
            calls.append(1)
            if len(calls) < 3:
                raise httpx.ReadTimeout("temporary")
            return httpx.Response(200, request=httpx.Request("GET", self.config.url), content=RSS_FIXTURE)

        items, error = fetch_feed(self.config, retries=2, request_fn=request, sleep_fn=lambda _seconds: None)
        self.assertEqual(len(calls), 3)
        self.assertIsNone(error)
        self.assertEqual(items[0].title, "AI launch")

    def test_fetch_returns_error_after_retries(self) -> None:
        calls = []

        def request(_url, _timeout, _headers):
            calls.append(1)
            raise httpx.ConnectError("offline")

        items, error = fetch_feed(self.config, retries=2, request_fn=request, sleep_fn=lambda _seconds: None)
        self.assertEqual(items, [])
        self.assertEqual(len(calls), 3)
        self.assertIn("Fixture", error or "")

    def test_fetch_all_keeps_partial_success_and_stats(self) -> None:
        good = SourceConfig("媒体新闻", "Good", "https://example.com/good")
        bad = SourceConfig("官方公告", "Bad", "https://example.com/bad")

        def request(url, _timeout, _headers):
            if url.endswith("bad"):
                raise httpx.ConnectError("offline")
            return httpx.Response(200, request=httpx.Request("GET", url), content=RSS_FIXTURE)

        items, errors, stats = fetch_all_with_stats([good, bad], retries=1, request_fn=request, sleep_fn=lambda _seconds: None)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual([entry["status"] for entry in stats], ["ok", "failed"])
        self.assertEqual(stats[1]["attempts"], 2)
