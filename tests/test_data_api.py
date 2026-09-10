import unittest

import httpx

from hotspot_agent.core import AccountProfile
from hotspot_agent.data_api import TAVILY_SEARCH_URL, fetch_supplemental_news


class DataAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = {"data_api_key": "test-key", "data_api_enabled": True}
        self.profile = AccountProfile.from_form(focus_topics="agents, developer tools")

    def test_tavily_results_are_normalized_with_original_provenance(self) -> None:
        captured = {}

        def request(url, payload, headers, timeout):
            captured.update({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "results": [{
                        "title": "New AI agent platform ships",
                        "url": "https://example.com/agent-news",
                        "content": "A product team released a grounded workflow update.",
                        "published_date": "2026-08-22T08:00:00Z",
                    }],
                    "usage": {"credits": 1},
                },
            )

        items, error, stats = fetch_supplemental_news(
            self.settings,
            self.profile,
            "live",
            request_fn=request,
        )
        self.assertIsNone(error)
        self.assertEqual(captured["url"], TAVILY_SEARCH_URL)
        self.assertEqual(captured["payload"]["topic"], "news")
        self.assertEqual(captured["payload"]["time_range"], "week")
        self.assertTrue(captured["headers"]["Authorization"].startswith("Bearer "))
        self.assertEqual(items[0].source_type, "数据检索")
        self.assertEqual(items[0].source_name, "example.com · Tavily")
        self.assertEqual(items[0].url, "https://example.com/agent-news")
        self.assertEqual(stats["status"], "ok")
        self.assertEqual(stats["usage"], {"credits": 1})

    def test_missing_key_skips_data_api_without_error(self) -> None:
        items, error, stats = fetch_supplemental_news(
            {"data_api_key": "", "data_api_enabled": True},
            self.profile,
            "live",
        )
        self.assertEqual(items, [])
        self.assertIsNone(error)
        self.assertIsNone(stats)

    def test_data_api_failure_is_isolated(self) -> None:
        def request(_url, _payload, _headers, _timeout):
            raise httpx.ConnectError("offline")

        items, error, stats = fetch_supplemental_news(
            self.settings,
            self.profile,
            "domestic",
            request_fn=request,
        )
        self.assertEqual(items, [])
        self.assertIn("Tavily Search", error or "")
        self.assertEqual(stats["status"], "failed")
        self.assertEqual(stats["failure_count"], 1)


if __name__ == "__main__":
    unittest.main()
