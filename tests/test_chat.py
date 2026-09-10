from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from hotspot_agent.chat import parse_chat_request
from hotspot_agent.chat_store import ChatStore


class ChatParserTests(unittest.TestCase):
    def test_missing_model_uses_demo_fallback_plan(self) -> None:
        parsed = parse_chat_request(
            "抓取最近 24 小时 AI Agent 相关热点并生成候选",
            settings={"enabled": True, "api_key": "", "model": "demo"},
        )
        self.assertFalse(parsed.used_model)
        self.assertEqual(parsed.plan["source_mode"], "demo")
        self.assertEqual(parsed.plan["output_mode"], "candidate_pool")
        self.assertTrue(parsed.plan["needs_confirmation"])
        self.assertIn("未配置", parsed.fallback_notice)

    def test_model_json_is_normalized_to_supported_values(self) -> None:
        response = type("Response", (), {
            "choices": [type("Choice", (), {
                "message": type("Message", (), {
                    "content": '{"query":"agents","source_mode":"live","time_range":"last_7_days","output_mode":"summary"}'
                })()
            })()]
        })()
        client = type("Client", (), {
            "chat": type("Chat", (), {
                "completions": type("Completions", (), {
                    "create": lambda *_args, **_kwargs: response
                })()
            })()
        })()
        with patch("openai.OpenAI", return_value=client):
            parsed = parse_chat_request(
                "请找 agents 新闻",
                settings={"enabled": True, "api_key": "key", "model": "demo"},
            )
        self.assertTrue(parsed.used_model)
        self.assertEqual(parsed.plan["source_mode"], "live")
        self.assertEqual(parsed.plan["output_mode"], "summary")


class ChatStoreTests(unittest.TestCase):
    def test_task_claim_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ChatStore(Path(directory) / "chat.db") as store:
                session_id = store.create_session()
                message_id = store.save_message(session_id, "user", "scan agents")
                task_id = store.create_task(session_id, message_id, {"source_mode": "demo"})
                self.assertTrue(store.claim_task(task_id))
                self.assertFalse(store.claim_task(task_id))
                self.assertEqual(store.get_task(task_id)["status"], "queued")


class ChatApiTests(unittest.TestCase):
    def test_chat_confirm_and_background_scan_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            os.environ["HOTSPOT_DB_PATH"] = str(Path(directory) / "app.db")
            with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
                from app import app

                with TestClient(app) as client:
                    page = client.get("/candidates")
                    self.assertEqual(page.status_code, 200)
                    self.assertIn("data-chat-panel", page.text)
                    response = client.post(
                        "/api/chat/messages",
                        json={"message": "抓取最近 24 小时 AI Agent 相关热点并生成候选"},
                    )
                    self.assertEqual(response.status_code, 200)
                    body = response.json()
                    self.assertEqual(body["status"], "awaiting_confirmation")
                    self.assertEqual(body["plan"]["source_mode"], "demo")
                    task_id = body["task_id"]

                    confirmed = client.post(f"/api/tasks/{task_id}/confirm")
                    self.assertEqual(confirmed.status_code, 200)
                    self.assertIn(confirmed.json()["status"], {"queued", "running", "succeeded"})

                    final = None
                    for _ in range(100):
                        task = client.get(f"/api/tasks/{task_id}").json()
                        if task["status"] not in {"queued", "running"}:
                            final = task
                            break
                        time.sleep(0.03)
                    self.assertIsNotNone(final)
                    self.assertEqual(final["status"], "succeeded")
                    self.assertGreater(final["result"]["candidate_count"], 0)
            os.environ.pop("HOTSPOT_DB_PATH", None)


if __name__ == "__main__":
    unittest.main()
