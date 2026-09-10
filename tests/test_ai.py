import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hotspot_agent.ai import enrich_candidate
from hotspot_agent.core import apply_model_assessment, analyze_items
from hotspot_agent.sources import demo_items


class AIFallbackTests(unittest.TestCase):
    def test_nonzero_model_adjustment_requires_a_reason(self) -> None:
        event = analyze_items(demo_items()[:1])[0].event
        original_score = event.priority_score
        apply_model_assessment(event, 8, "", 90)
        self.assertEqual(event.model_adjustment, 0)
        self.assertEqual(event.priority_score, original_score)
        self.assertEqual(event.model_confidence, 90)

    def test_missing_key_keeps_template_candidate(self) -> None:
        previous = os.environ.pop("OPENAI_API_KEY", None)
        try:
            candidate = analyze_items(demo_items()[:1])[0]
            original_copy = candidate.english_copy
            result = enrich_candidate(candidate, "AI product account")
            self.assertEqual(result.ai_status, "template")
            self.assertEqual(result.english_copy, original_copy)
        finally:
            if previous is not None:
                os.environ["OPENAI_API_KEY"] = previous

    def test_compatible_api_success_updates_and_bounds_candidate(self) -> None:
        previous = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "test-key"
        try:
            candidate = analyze_items(demo_items()[:1])[0]
            candidate.event.risk_flags = ["Keep deterministic risk"]
            payload = json.dumps({
                "optimized_title": "AI agents gain safer approval controls",
                "optimized_summary": "The supplied sources describe a concrete approval workflow update.",
                "content_angle": "Product workflow impact",
                "english_copy": "Useful grounded update. " * 30,
                "risk_flags": ["Verify the claim"],
                "selection_adjustment": 99,
                "selection_reason": "Strong account relevance with source evidence.",
                "selection_confidence": 120,
                "review_decision": "pass",
                "review_reason": "The event is relevant and supported by the supplied evidence.",
                "review_confidence": 88,
            })
            response = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
            )
            client = MagicMock()
            client.chat.completions.create.return_value = response
            with patch("openai.OpenAI", return_value=client):
                result = enrich_candidate(candidate, "AI product account")
            self.assertEqual(result.ai_status, "模型评分+整理+审核")
            self.assertEqual(result.optimized_title, "AI agents gain safer approval controls")
            self.assertEqual(result.ai_review_status, "passed")
            self.assertEqual(result.ai_review_confidence, 88)
            self.assertLessEqual(len(result.english_copy), 280)
            self.assertEqual(result.content_angle, "Product workflow impact")
            self.assertEqual(result.event.risk_flags, ["Keep deterministic risk", "Verify the claim"])
            self.assertEqual(result.event.model_adjustment, 10)
            self.assertEqual(result.event.model_confidence, 100)
            self.assertEqual(result.event.model_reason, "Strong account relevance with source evidence.")
            self.assertEqual(
                result.event.priority_score,
                min(
                    100,
                    result.event.base_priority_score
                    + result.event.profile_adjustment
                    + result.event.feedback_adjustment
                    + 10,
                ),
            )
            client.chat.completions.create.assert_called_once()
        finally:
            if previous is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous

    def test_ai_review_pass_requires_threshold_and_reason(self) -> None:
        candidate = analyze_items(demo_items()[:1])[0]
        payload = json.dumps({
            "review_decision": "pass",
            "review_reason": "Evidence is relevant but confidence is limited.",
            "review_confidence": 69,
        })
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=payload))])
        client = MagicMock()
        client.chat.completions.create.return_value = response
        settings = {
            "api_key": "test-key", "model": "test-model", "enabled": True,
            "scoring_enabled": False, "content_enabled": False, "review_enabled": True,
            "review_threshold": 70,
        }
        with patch("openai.OpenAI", return_value=client):
            result = enrich_candidate(candidate, "AI product account", settings=settings)
        self.assertEqual(result.ai_review_status, "rejected")
        self.assertEqual(result.ai_review_confidence, 69)
