import unittest

import httpx

from hotspot_agent.content import ContentResult, enrich_items, extract_article
from hotspot_agent.core import SourceItem, assess_item_quality


HTML_FIXTURE = b"""
<html><head><title>Example</title></head><body>
<nav>Navigation should be ignored</nav>
<article><h1>AI workflow update</h1><p>This is a long article body about AI agents and practical workflows for product teams.</p>
<p>It contains enough context for a reviewer to understand the change, the users affected, and the next decision to make.</p></article>
<script>ignore()</script>
</body></html>
"""


class ContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.item = SourceItem(
            "媒体新闻", "Fixture", "AI workflow update", "Short RSS summary",
            "https://example.com/article", "2026-08-20T08:00:00+00:00",
        )

    def test_html_article_is_extracted_and_noise_is_ignored(self) -> None:
        def request(url, _timeout, _headers):
            return httpx.Response(200, request=httpx.Request("GET", url), content=HTML_FIXTURE,
                                  headers={"content-type": "text/html; charset=utf-8"})

        result = extract_article(self.item.url, request_fn=request, sleep_fn=lambda _seconds: None)
        self.assertEqual(result.status, "extracted")
        self.assertIn("long article body", result.content)
        self.assertNotIn("Navigation", result.content)

    def test_failed_extraction_keeps_rss_summary(self) -> None:
        calls = []

        def request(url, _timeout, _headers):
            calls.append(url)
            raise httpx.ReadTimeout("offline")

        items, errors = enrich_items([self.item], request_fn=request, sleep_fn=lambda _seconds: None)
        self.assertEqual(len(calls), 2)
        self.assertEqual(items[0].content, "Short RSS summary")
        self.assertEqual(items[0].content_status, "summary_fallback")
        self.assertTrue(errors)

    def test_same_url_is_requested_once(self) -> None:
        second = SourceItem("社区趋势", "Other", "AI workflow update", "Another summary", self.item.url, self.item.published_at)
        calls = []

        def request(url, _timeout, _headers):
            calls.append(url)
            return httpx.Response(200, request=httpx.Request("GET", url), content=HTML_FIXTURE,
                                  headers={"content-type": "text/html; charset=utf-8"})

        items, errors = enrich_items([self.item, second], request_fn=request, sleep_fn=lambda _seconds: None)
        self.assertEqual(len(calls), 1)
        self.assertFalse(errors)
        self.assertEqual(items[0].content, items[1].content)

    def test_quality_flags_summary_fallback(self) -> None:
        score, flags = assess_item_quality(self.item)
        self.assertLess(score, 80)
        self.assertIn("正文未抽取，使用 RSS 摘要", flags)

    def test_extraction_is_bounded_per_source(self) -> None:
        items = [
            SourceItem(
                "媒体新闻", "Fixture", f"AI workflow update {index}", "RSS summary",
                f"https://example.com/article-{index}", "2026-08-20T08:00:00+00:00",
            )
            for index in range(5)
        ]
        calls = []

        def request(url, _timeout, _headers):
            calls.append(url)
            return httpx.Response(200, request=httpx.Request("GET", url), content=HTML_FIXTURE,
                                  headers={"content-type": "text/html; charset=utf-8"})

        enriched, errors = enrich_items(
            items, request_fn=request, sleep_fn=lambda _seconds: None,
            max_per_source=2, max_workers=2,
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(sum(item.content_status == "extracted" for item in enriched), 2)
        self.assertEqual(sum(item.content_status == "skipped" for item in enriched), 3)
        self.assertFalse(errors)


if __name__ == "__main__":
    unittest.main()
