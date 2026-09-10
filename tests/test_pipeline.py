import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from hotspot_agent.pipeline import run_scan
from hotspot_agent.sources import DEFAULT_SOURCES, SourceConfig, demo_items
from hotspot_agent.storage import Store


class OpenAICompatibleHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        content = json.dumps({
            "optimized_title": "AI 整理后的热点标题",
            "optimized_summary": "模型根据来源正文生成的事实摘要。",
            "content_angle": "Grounded product workflow impact",
            "english_copy": "A concise AI-enhanced candidate grounded in the supplied event.",
            "risk_flags": ["Verify details before publishing"],
            "selection_adjustment": 6,
            "selection_reason": "Relevant and sufficiently grounded for the target account.",
            "selection_confidence": 84,
            "review_decision": "pass",
            "review_reason": "The supplied evidence is relevant and sufficiently grounded.",
            "review_confidence": 86,
        })
        body = json.dumps({
            "id": "chatcmpl-delivery-test",
            "object": "chat.completion",
            "created": 0,
            "model": "delivery-test-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args) -> None:
        return


class PipelineMonitoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DEFAULT_SOURCES[0]
        self.stats = [{
            "source_type": self.config.source_type, "source_name": self.config.source_name, "url": self.config.url,
            "status": "ok", "attempts": 1, "item_count": 1, "success_count": 1,
            "failure_count": 0, "content_success_count": 1, "content_failure_count": 0,
            "content_skipped_count": 0, "fallback_count": 0, "duration_ms": 5,
            "error": "", "fallback_used": False,
        }]

    def test_partial_success_persists_source_health(self) -> None:
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / "run.db") as store:
            with patch("hotspot_agent.pipeline.fetch_all_with_stats", return_value=([], [], self.stats)), patch(
                "hotspot_agent.pipeline.enrich_items", side_effect=lambda items, **_kwargs: (items, [])
            ):
                # The mocked fetch result is intentionally empty, so this verifies
                # the deterministic fallback and its run-level accounting.
                run_scan("live", store=store, configs=[self.config])
            run = store.list_runs()[0]
            self.assertTrue(run["fallback_used"])
            self.assertEqual(run["source_stats"][0]["fallback_count"], 2)
            self.assertEqual(run["source_stats"][0]["status"], "fallback")

    def test_live_items_keep_content_stats_without_fallback(self) -> None:
        from hotspot_agent.sources import demo_items

        items = demo_items()[:1]
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / "run.db") as store:
            with patch("hotspot_agent.pipeline.fetch_all_with_stats", return_value=(items, [], self.stats)), patch(
                "hotspot_agent.pipeline.enrich_items", side_effect=lambda values, **_kwargs: (values, [])
            ):
                run_scan("live", store=store, configs=[self.config])
            run = store.list_runs()[0]
            self.assertFalse(run["fallback_used"])
            self.assertEqual(run["source_stats"][0]["status"], "ok")
            self.assertEqual(run["source_stats"][0]["item_count"], 1)

    def test_content_stats_are_calculated_for_each_source(self) -> None:
        from hotspot_agent.sources import demo_items

        configs = DEFAULT_SOURCES[:2]
        items = demo_items()[:2]
        stats = [
            {
                "source_type": config.source_type, "source_name": config.source_name, "url": config.url,
                "status": "ok", "attempts": 1, "item_count": 1, "success_count": 1,
                "failure_count": 0, "content_success_count": 0, "content_failure_count": 0,
                "content_skipped_count": 0, "fallback_count": 0, "duration_ms": 5,
                "error": "", "fallback_used": False,
            }
            for config in configs
        ]

        def enrich(values, **_kwargs):
            values[0].content_status = "extracted"
            values[1].content_status = "skipped"
            return values, []

        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / "run.db") as store:
            with patch("hotspot_agent.pipeline.fetch_all_with_stats", return_value=(items, [], stats)), patch(
                "hotspot_agent.pipeline.enrich_items", side_effect=enrich
            ):
                run_scan("live", store=store, configs=configs)
            source_stats = {entry["source_name"]: entry for entry in store.list_runs()[0]["source_stats"]}
            self.assertEqual(source_stats[configs[0].source_name]["content_success_count"], 1)
            self.assertEqual(source_stats[configs[1].source_name]["content_skipped_count"], 1)

    def test_successful_ai_enhancement_is_persisted_through_pipeline(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), OpenAICompatibleHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        previous = {key: os.environ.get(key) for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")}
        os.environ["OPENAI_API_KEY"] = "delivery-test-key"
        os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{server.server_port}/v1"
        os.environ["OPENAI_MODEL"] = "delivery-test-model"
        try:
            with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / "run.db") as store:
                run_scan("demo", store=store)
                rows = store.list_candidates()
                self.assertTrue(rows)
                self.assertTrue(all(row["ai_status"] == "模型评分+整理+审核" for row in rows))
                self.assertTrue(all(row["english_copy"].startswith("A concise AI-enhanced") for row in rows))
                self.assertTrue(all(row["title"] == "AI 整理后的热点标题" for row in rows))
                self.assertTrue(all(row["ai_review_status"] == "passed" for row in rows))
                self.assertEqual(len(store.list_candidates("pending")), len(rows))
                self.assertTrue(all(row["model_adjustment"] == 6 for row in rows))
                self.assertTrue(all(row["model_confidence"] == 84 for row in rows))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_saved_candidate_limit_controls_model_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / "run.db") as store:
            store.save_app_settings({
                "model_api_key": "configured-key",
                "model_enabled": 1,
                "model_scoring_enabled": 1,
                "model_content_enabled": 0,
                "model_candidate_limit": 1,
            })
            with patch("hotspot_agent.pipeline.enrich_candidate", side_effect=lambda candidate, *_args, **_kwargs: candidate) as mocked:
                run_scan("demo", store=store)
            self.assertEqual(mocked.call_count, 1)

    def test_supplemental_data_api_items_join_the_scan(self) -> None:
        rss_items = demo_items()[:1]
        supplemental = demo_items()[1:2]
        supplemental[0].source_type = "数据检索"
        supplemental[0].source_name = "example.com · Tavily"
        supplemental_stats = {
            "source_type": "数据检索", "source_name": "Tavily Search", "url": "https://api.tavily.com/search",
            "status": "ok", "attempts": 1, "item_count": 1, "success_count": 1,
            "failure_count": 0, "content_success_count": 0, "content_failure_count": 0,
            "content_skipped_count": 0, "fallback_count": 0, "duration_ms": 5,
            "error": "", "fallback_used": False,
        }
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / "run.db") as store:
            store.save_app_settings({"data_api_key": "configured-key", "data_api_enabled": 1})
            with patch("hotspot_agent.pipeline.fetch_all_with_stats", return_value=(rss_items, [], self.stats)), patch(
                "hotspot_agent.pipeline.fetch_supplemental_news",
                return_value=(supplemental, None, supplemental_stats),
            ), patch("hotspot_agent.pipeline.enrich_items", side_effect=lambda values, **_kwargs: (values, [])):
                run_scan("live", store=store, configs=[self.config])
            run = store.list_runs()[0]
            self.assertEqual(run["item_count"], 2)
            self.assertFalse(run["fallback_used"])
            self.assertEqual([entry["source_name"] for entry in run["source_stats"]], [self.config.source_name, "Tavily Search"])
            source_types = {
                source["source_type"]
                for candidate in store.list_candidates()
                for source in candidate["sources"]
            }
            self.assertIn("数据检索", source_types)


if __name__ == "__main__":
    unittest.main()
