import tempfile
import unittest
import sqlite3
from pathlib import Path

from hotspot_agent.core import analyze_items
from hotspot_agent.sources import SourceConfig, demo_items
from hotspot_agent.storage import Store


class StorageTests(unittest.TestCase):
    def test_candidate_origin_uses_run_mode_and_demo_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "origins.db"
            configs = [SourceConfig("媒体新闻", "TechCrunch AI", "https://techcrunch.com/feed")]
            items = demo_items()[:1]
            candidates = analyze_items(items)
            cases = [
                ("demo", False, "demo", "演示快照"),
                ("live", False, "overseas", "海外来源"),
                ("domestic", False, "domestic", "国内来源"),
                ("domestic", True, "demo", "演示快照"),
            ]
            with Store(path) as store:
                for mode, fallback_used, expected_origin, expected_label in cases:
                    with self.subTest(mode=mode, fallback_used=fallback_used):
                        store.save_run(
                            mode, configs, items, candidates, [], fallback_used=fallback_used
                        )
                        candidate = store.get_candidate(candidates[0].candidate_id)
                        self.assertEqual(candidate["source_origin"], expected_origin)
                        self.assertEqual(candidate["source_origin_label"], expected_label)

    def test_approved_candidate_can_be_deleted_and_restored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delete.db"
            configs = [SourceConfig("媒体新闻", "TechCrunch AI", "https://techcrunch.com/feed")]
            items = demo_items()[:1]
            candidates = analyze_items(items)
            with Store(path) as store:
                store.save_run("demo", configs, items, candidates, [])
                candidate_id = candidates[0].candidate_id
                store.review(candidate_id, "approved", "Approved copy", "Ready")
                store.delete_approved(candidate_id)

                self.assertEqual(store.list_candidates(), [])
                self.assertEqual(store.list_candidates("approved"), [])
                self.assertEqual(store.list_candidates("deleted")[0]["candidate_id"], candidate_id)
                self.assertEqual(store.status_counts()["deleted"], 1)
                self.assertEqual(store.feedback_signals()[0].action, "approved")
                with self.assertRaises(ValueError):
                    store.review(candidate_id, "approved", "Changed", "")

                store.save_run("demo", configs, items, candidates, [])
                self.assertEqual(store.list_candidates("deleted")[0]["candidate_id"], candidate_id)
                store.restore(candidate_id)
                self.assertEqual(store.list_candidates("approved")[0]["candidate_id"], candidate_id)
                self.assertEqual(store.status_counts()["deleted"], 0)

    def test_non_approved_candidate_cannot_be_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delete.db"
            configs = [SourceConfig("媒体新闻", "TechCrunch AI", "https://techcrunch.com/feed")]
            items = demo_items()[:1]
            candidates = analyze_items(items)
            with Store(path) as store:
                store.save_run("demo", configs, items, candidates, [])
                with self.assertRaisesRegex(ValueError, "只有最终候选池"):
                    store.delete_approved(candidates[0].candidate_id)

    def test_ai_gate_then_human_review_creates_two_candidate_stages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stages.db"
            items = demo_items()[:2]
            candidates = analyze_items(items)
            candidates[0].ai_review_status = "passed"
            candidates[0].ai_review_reason = "Relevant and grounded."
            candidates[0].ai_review_confidence = 88
            candidates[1].ai_review_status = "rejected"
            candidates[1].ai_review_reason = "Insufficient evidence."
            candidates[1].ai_review_confidence = 81
            with Store(path) as store:
                store.save_run("demo", [], items, candidates, [])
                self.assertEqual(
                    [row["candidate_id"] for row in store.list_candidates("pending")],
                    [candidates[0].candidate_id],
                )
                self.assertEqual(
                    [row["candidate_id"] for row in store.list_candidates("ai_filtered")],
                    [candidates[1].candidate_id],
                )
                with self.assertRaisesRegex(ValueError, "尚未通过 AI"):
                    store.review(candidates[1].candidate_id, "approved", "Blocked", "")
                store.review(candidates[0].candidate_id, "approved", "Final copy", "Human verified")
                final_rows = store.list_candidates("final")
                self.assertEqual([row["candidate_id"] for row in final_rows], [candidates[0].candidate_id])
                self.assertEqual(final_rows[0]["workflow_stage"], "final")

    def test_run_and_review_are_persisted_in_split_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            configs = [SourceConfig("媒体新闻", "TechCrunch AI", "https://techcrunch.com/feed")]
            items = demo_items()[:2]
            candidates = analyze_items(items)
            with Store(path) as store:
                items[0].content = "Extracted article body " * 50
                items[0].content_status = "extracted"
                items[0].content_source = "article"
                run_id = store.save_run(
                    "demo", configs, items, candidates, [],
                    source_stats=[{"source_name": "TechCrunch AI", "status": "demo", "item_count": 2}],
                    duration_ms=123,
                )
                rows = store.list_candidates()
                self.assertEqual(run_id, 1)
                self.assertTrue(rows[0]["sources"])
                store.review(rows[0]["candidate_id"], "edited", "Edited copy", "Adjusted claim")
                detail = store.get_candidate(rows[0]["candidate_id"])
                self.assertEqual(detail["status"], "edited")
                self.assertEqual(detail["review_history"][0]["english_copy"], "Edited copy")
                self.assertEqual(detail["review_history"][0]["note"], "Adjusted claim")
                self.assertEqual(store.status_counts()["edited"], 1)
                signals = store.feedback_signals()
                self.assertEqual(len(signals), 1)
                self.assertEqual(signals[0].action, "edited")
                self.assertEqual(store.list_runs()[0]["candidate_count"], len(candidates))
                self.assertEqual(store.list_runs()[0]["source_stats"][0]["status"], "demo")
                self.assertEqual(store.list_runs()[0]["duration_ms"], 123)
                self.assertEqual(
                    store.connection.execute(
                        "SELECT content_status FROM articles WHERE article_id = ?", (items[0].item_id,)
                    ).fetchone()[0],
                    "extracted",
                )
                store.save_run("demo", configs, items, candidates, [])
                after_rescan = store.get_candidate(rows[0]["candidate_id"])
                self.assertEqual(after_rescan["status"], "edited")
                self.assertEqual(after_rescan["english_copy"], "Edited copy")
            self.assertTrue(path.exists())

    def test_legacy_database_gets_additive_score_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE events (
                    event_id TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL,
                    priority_score INTEGER NOT NULL, priority_label TEXT NOT NULL,
                    follow_decision TEXT NOT NULL, follow_reason TEXT NOT NULL,
                    risk_flags_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL,
                    started_at TEXT NOT NULL, source_count INTEGER NOT NULL,
                    item_count INTEGER NOT NULL, candidate_count INTEGER NOT NULL,
                    errors_json TEXT NOT NULL DEFAULT '[]'
                );
                """
            )
            connection.execute(
                """INSERT INTO events(
                    event_id, title, summary, priority_score, priority_label,
                    follow_decision, follow_reason, risk_flags_json, created_at
                ) VALUES ('legacy-1', 'Legacy event', 'Old score', 61, '值得观察',
                          '建议评估', 'Legacy reason', '[]', '2026-08-20T00:00:00+00:00')"""
            )
            connection.commit()
            connection.close()
            with Store(path) as store:
                event_columns = {row[1] for row in store.connection.execute("PRAGMA table_info(events)")}
                run_columns = {row[1] for row in store.connection.execute("PRAGMA table_info(runs)")}
                self.assertTrue({
                    "base_score", "profile_adjustment", "feedback_adjustment",
                    "model_adjustment", "model_reason", "model_confidence",
                    "heat_score", "credibility_score", "relevance_score",
                    "recommendation", "recommendation_reason",
                } <= event_columns)
                self.assertIn("profile_json", run_columns)
                self.assertIn("trigger_type", run_columns)
                candidate_columns = {row[1] for row in store.connection.execute("PRAGMA table_info(candidates)")}
                self.assertTrue({
                    "deleted_at", "optimized_title", "optimized_summary", "ai_review_status",
                    "ai_review_reason", "ai_review_confidence",
                } <= candidate_columns)
                article_columns = {row[1] for row in store.connection.execute("PRAGMA table_info(articles)")}
                self.assertTrue({
                    "engagement_points", "engagement_comments", "heat_score",
                } <= article_columns)
                self.assertIsNotNone(store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'scheduled_scans'"
                ).fetchone())
                self.assertEqual(store.connection.execute("SELECT base_score FROM events WHERE event_id = 'legacy-1'").fetchone()[0], 61)

    def test_api_settings_are_stored_locally_and_can_be_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / "settings.db") as store:
            store.save_app_settings({
                "model_api_key": "model-secret",
                "model_enabled": 1,
                "data_api_key": "data-secret",
            })
            settings = store.app_settings()
            self.assertEqual(settings["model_api_key"], "model-secret")
            self.assertEqual(settings["data_api_key"], "data-secret")
            store.save_app_settings({"model_api_key": ""})
            self.assertEqual(store.app_settings()["model_api_key"], "")
