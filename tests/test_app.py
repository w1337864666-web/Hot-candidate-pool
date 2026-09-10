import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from hotspot_agent.sources import DOMESTIC_SOURCES
from hotspot_agent.storage import Store


class AppTests(unittest.TestCase):
    def test_health_and_demo_scan_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            os.environ["HOTSPOT_DB_PATH"] = str(Path(directory) / "app.db")
            from app import app

            with TestClient(app) as client:
                self.assertEqual(client.get("/health").json()["status"], "ok")
                response = client.post(
                    "/scan",
                    data={
                        "mode": "demo",
                        "profile": "AI product account",
                        "audience": "Developers",
                        "focus_topics": "agents, developer tools",
                        "avoid_topics": "rumors",
                        "tone": "Clear and useful",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 303)
                self.assertRegex(response.headers["location"], r"^/candidates\?scan=\d+$")
                page = client.get("/candidates")
                self.assertEqual(page.status_code, 200)
                self.assertIn("热点工作流", page.text)
                self.assertIn("双重审核流程", page.text)
                self.assertIn("人工终审", page.text)
                self.assertIn("人工终审工作区", page.text)
                self.assertIn("候选一", page.text)
                self.assertIn("候选二", page.text)
                self.assertIn("模型、数据与调度", page.text)
                self.assertIn('href="/settings/api"', page.text)
                self.assertIn('data-sidebar="collapsed"', page.text)
                self.assertIn("data-sidebar-toggle", page.text)
                self.assertIn('aria-controls="app-sidebar"', page.text)
                self.assertIn('/static/ui.js?v=2', page.text)
                self.assertIn('/static/style.css?v=21', page.text)
                self.assertIn('class="scan-advanced"', page.text)
                self.assertIn('value="demo"', page.text)
                self.assertIn('value="live"', page.text)
                self.assertIn('value="domestic"', page.text)
                self.assertIn("海外来源", page.text)
                self.assertIn("国内来源", page.text)
                self.assertIn('class="origin-badge origin-demo"', page.text)
                self.assertIn('data-origin-tab="all"', page.text)
                self.assertIn('data-origin-tab="domestic"', page.text)
                self.assertIn('class="origin-group origin-group-demo"', page.text)
                self.assertIn('aria-label="演示快照候选事件列表"', page.text)
                self.assertIn('aria-current="page"', page.text)
                self.assertIn('class="workflow-strip"', page.text)
                self.assertIn('class="status status-first"', page.text)
                self.assertIn("规则初筛兜底", page.text)
                demo_origin_page = client.get("/candidates?origin=demo")
                self.assertIn('data-origin-tab="demo" aria-current="page"', demo_origin_page.text)
                self.assertIn('class="origin-group origin-group-demo"', demo_origin_page.text)
                domestic_origin_page = client.get("/candidates?origin=domestic")
                self.assertIn("当前分类没有热点", domestic_origin_page.text)
                self.assertNotIn('class="origin-group origin-group-demo"', domestic_origin_page.text)
                scan_page = client.get(response.headers["location"])
                self.assertIn("已载入演示快照", scan_page.text)
                self.assertIn("本次使用固定离线数据", scan_page.text)
                with Store() as store:
                    fallback_run_id = store.save_run(
                        "live",
                        [],
                        [],
                        [],
                        ["TechCrunch AI: WinError 10013"],
                        source_stats=[
                            {"source_name": "TechCrunch AI", "status": "failed"},
                            {"source_name": "OpenAI News", "status": "failed"},
                            {"source_name": "Hacker News AI", "status": "failed"},
                        ],
                        fallback_used=True,
                    )
                fallback_page = client.get(f"/candidates?scan={fallback_run_id}")
                self.assertIn("真实扫描未获取到数据，已回退演示快照", fallback_page.text)
                self.assertIn("网络层拒绝了出站连接", fallback_page.text)
                self.assertIn("TechCrunch AI、OpenAI News、Hacker News AI", fallback_page.text)
                ui_script = client.get("/static/ui.js")
                self.assertEqual(ui_script.status_code, 200)
                self.assertIn("hotspot.sidebar.expanded", ui_script.text)
                with Store() as store:
                    next_candidate_id = store.next_pending_candidate_id()
                    self.assertEqual(next_candidate_id, store.list_candidates("pending")[0]["candidate_id"])
                    next_review = client.get("/review/next", follow_redirects=False)
                    self.assertEqual(next_review.status_code, 303)
                    self.assertEqual(next_review.headers["location"], f"/candidates/{next_candidate_id}")
                    candidate = store.list_candidates()[0]
                    pending_before_review = len(store.list_candidates("pending"))
                    detail_page = client.get(f"/candidates/{candidate['candidate_id']}")
                    self.assertNotIn('class="lead"', detail_page.text)
                    self.assertIn('class="origin-badge origin-demo"', detail_page.text)
                    self.assertIn("评分构成", detail_page.text)
                    self.assertIn("保存并审核下一条", detail_page.text)
                    self.assertIn("第一重 · AI 智能审核", detail_page.text)
                    self.assertIn("第二重 · 人工终审", detail_page.text)
                    self.assertIn("整理后的热点摘要", detail_page.text)
                    self.assertIn("账号相关性", detail_page.text)
                    self.assertIn("来源可信度", detail_page.text)
                    self.assertIn("推荐判断理由", detail_page.text)
                    self.assertIn('type="radio"', detail_page.text)
                    self.assertIn('class="decision-bar"', detail_page.text)
                    self.assertIn('data-pending-label="保存中"', detail_page.text)
                    store.review(candidate["candidate_id"], "approved", candidate["english_copy"], "Ready")
                    candidate_id = candidate["candidate_id"]
                    second_candidate_id = store.next_pending_candidate_id()
                    self.assertIsNotNone(second_candidate_id)

                next_candidate = client.post(
                    f"/candidates/{second_candidate_id}/review",
                    data={
                        "status": "approved",
                        "english_copy": "Reviewed copy",
                        "note": "Ready",
                        "next_action": "next",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(next_candidate.status_code, 303)
                self.assertEqual(next_candidate.headers["location"], "/review/next")
                with Store() as store:
                    self.assertEqual(len(store.list_candidates("pending")), pending_before_review - 2)
                    self.assertEqual(len(store.list_candidates("final")), 2)

                stay_on_candidate = client.post(
                    f"/candidates/{second_candidate_id}/review",
                    data={"status": "edited", "english_copy": "Updated copy", "note": "Adjusted"},
                    follow_redirects=False,
                )
                self.assertEqual(stay_on_candidate.headers["location"], f"/candidates/{second_candidate_id}")
                invalid_next_action = client.post(
                    f"/candidates/{second_candidate_id}/review",
                    data={"status": "edited", "english_copy": "Updated copy", "next_action": "other"},
                )
                self.assertEqual(invalid_next_action.status_code, 400)

                approved_page = client.get("/candidates?status=approved")
                self.assertIn("删除", approved_page.text)
                deleted = client.post(f"/candidates/{candidate_id}/delete", follow_redirects=False)
                self.assertEqual(deleted.status_code, 303)
                self.assertNotIn(candidate_id, client.get("/candidates?status=approved").text)
                deleted_page = client.get("/candidates?status=deleted")
                self.assertIn(candidate_id, deleted_page.text)
                self.assertIn("恢复", deleted_page.text)
                blocked_review = client.post(
                    f"/candidates/{candidate_id}/review",
                    data={"status": "approved", "english_copy": "Changed", "note": ""},
                )
                self.assertEqual(blocked_review.status_code, 400)
                restored = client.post(f"/candidates/{candidate_id}/restore", follow_redirects=False)
                self.assertEqual(restored.status_code, 303)
                self.assertIn(candidate_id, client.get("/candidates?status=approved").text)
                runs_page = client.get("/runs")
                self.assertEqual(runs_page.status_code, 200)
                self.assertIn("来源状态可追溯", runs_page.text)
                self.assertIn('class="runs-table"', runs_page.text)
                self.assertIn("自动扫描结果", runs_page.text)

                settings_page = client.get("/settings/api")
                self.assertEqual(settings_page.status_code, 200)
                self.assertIn("大模型能力", settings_page.text)
                self.assertIn("智能审核", settings_page.text)
                self.assertIn("补充数据检索", settings_page.text)
                self.assertIn("每日自动扫描", settings_page.text)
                self.assertIn('name="schedule_enabled"', settings_page.text)
                saved_settings = client.post(
                    "/settings/api",
                    data={
                        "model_api_key": "model-page-secret",
                        "model_base_url": "https://api.example.com/v1",
                        "model_name": "example-model",
                        "model_enabled": "on",
                        "model_scoring_enabled": "on",
                        "model_content_enabled": "on",
                        "model_review_enabled": "on",
                        "model_candidate_limit": "8",
                        "model_review_threshold": "75",
                        "data_api_key": "data-page-secret",
                        "data_api_enabled": "on",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(saved_settings.status_code, 303)
                self.assertEqual(saved_settings.headers["location"], "/settings/api?saved=1")
                configured_page = client.get(saved_settings.headers["location"])
                self.assertIn("能力与调度配置已保存", configured_page.text)
                self.assertIn("2 项能力已启用", configured_page.text)
                self.assertNotIn("model-page-secret", configured_page.text)
                self.assertNotIn("data-page-secret", configured_page.text)
                with Store() as store:
                    for pending in store.list_candidates("pending"):
                        store.review(pending["candidate_id"], "rejected", pending["english_copy"], "Done")
                empty_queue = client.get("/review/next", follow_redirects=False)
                self.assertEqual(empty_queue.headers["location"], "/candidates?status=pending")

            with Store() as store:
                demo_run_id = int(response.headers["location"].split("=", 1)[1])
                demo_run = next(run for run in store.list_runs() if run["run_id"] == demo_run_id)
                self.assertEqual(demo_run["profile"]["audience"], "Developers")
            os.environ.pop("HOTSPOT_DB_PATH", None)

    def test_domestic_scan_selects_domestic_source_preset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            os.environ["HOTSPOT_DB_PATH"] = str(Path(directory) / "app.db")
            from app import app

            with patch("app.run_scan", return_value=(7, [], [])) as mocked, TestClient(app) as client:
                response = client.post("/scan", data={"mode": "domestic"}, follow_redirects=False)

            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/candidates?scan=7")
            self.assertIs(mocked.call_args.kwargs["configs"], DOMESTIC_SOURCES)
            os.environ.pop("HOTSPOT_DB_PATH", None)
