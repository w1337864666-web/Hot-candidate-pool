import tempfile
import unittest
from pathlib import Path

from hotspot_agent.core import AccountProfile
from hotspot_agent.pipeline import run_scan
from hotspot_agent.storage import Store


class FeedbackLoopTests(unittest.TestCase):
    def test_second_scan_uses_previous_review_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with Store(Path(directory) / "feedback.db") as store:
                profile = AccountProfile.from_form(focus_topics="agents")
                _, _, first_candidates = run_scan("demo", profile, store=store)
                target = next(candidate for candidate in first_candidates if "agents" in candidate.event.title.lower())
                store.review(target.candidate_id, "approved", target.english_copy, "Useful for our audience")
                _, _, second_candidates = run_scan("demo", profile, store=store)
                updated = next(candidate for candidate in second_candidates if candidate.candidate_id == target.candidate_id)
                self.assertGreater(updated.event.feedback_adjustment, 0)
                self.assertGreater(updated.event.priority_score, updated.event.base_priority_score)
                self.assertIn("历史审核", updated.event.follow_reason)
