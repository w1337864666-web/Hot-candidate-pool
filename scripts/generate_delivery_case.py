from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hotspot_agent.core import AccountProfile, Candidate
from hotspot_agent.pipeline import run_scan
from hotspot_agent.storage import Store


PROFILE = AccountProfile.from_form(
    audience="English-speaking AI users, creators, and product teams",
    focus_topics="AI products, agents, developer tools, creator workflows",
    avoid_topics="rumors, unverified claims",
    tone="Concise, professional, grounded, and non-hype",
    free_text="Official overseas AI product account on X.",
)


def _snapshot(candidate: Candidate, rank: int) -> dict[str, object]:
    event = candidate.event
    return {
        "candidate_id": candidate.candidate_id,
        "rank": rank,
        "title": event.title,
        "summary": event.summary,
        "source_types": sorted({item.source_type for item in event.items}),
        "source_count": len(event.items),
        "base_score": event.base_priority_score,
        "heat_score": event.heat_score,
        "credibility_score": event.credibility_score,
        "relevance_score": event.relevance_score,
        "recommendation": event.recommendation,
        "recommendation_reason": event.recommendation_reason,
        "profile_adjustment": event.profile_adjustment,
        "feedback_adjustment": event.feedback_adjustment,
        "final_score": event.priority_score,
        "follow_decision": event.follow_decision,
        "english_copy": candidate.english_copy,
        "ai_status": candidate.ai_status,
    }


def _source_summary(run: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "source_type": item["source_type"],
            "source_name": item["source_name"],
            "status": item["status"],
            "item_count": item["item_count"],
            "attempts": item["attempts"],
            "fallback_used": item["fallback_used"],
        }
        for item in run["source_stats"]
    ]


def main() -> None:
    # The deterministic path is the reproducible delivery baseline. AI remains optional.
    os.environ["OPENAI_API_KEY"] = ""
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "delivery-case.db"
        with Store(database) as store:
            first_run_id, first_errors, first_candidates = run_scan("live", PROFILE, store=store)
            eligible = [
                candidate
                for candidate in first_candidates
                if candidate.event.profile_adjustment > 0
                and candidate.event.follow_decision != "暂不跟进"
                and candidate.event.priority_score < 96
            ]
            target = eligible[0] if eligible else first_candidates[0]
            before_rank = next(
                index for index, candidate in enumerate(first_candidates, start=1)
                if candidate.candidate_id == target.candidate_id
            )
            before = _snapshot(target, before_rank)

            store.review(
                target.candidate_id,
                "approved",
                target.english_copy,
                "Delivery case: relevant and grounded for the account audience.",
            )

            second_run_id, second_errors, second_candidates = run_scan("live", PROFILE, store=store)
            after_candidate = next(
                (candidate for candidate in second_candidates if candidate.candidate_id == target.candidate_id),
                None,
            )
            if after_candidate is None:
                after_candidate = next(
                    candidate for candidate in second_candidates if candidate.event.title == target.event.title
                )
            after_rank = next(
                index for index, candidate in enumerate(second_candidates, start=1)
                if candidate.candidate_id == after_candidate.candidate_id
            )
            after = _snapshot(after_candidate, after_rank)

            runs = {run["run_id"]: run for run in store.list_runs()}
            review_history = store.get_candidate(target.candidate_id)["review_history"]
            result = {
                "profile": PROFILE.to_dict(),
                "first_run": {
                    "run_id": first_run_id,
                    "mode": "live",
                    "source_stats": _source_summary(runs[first_run_id]),
                    "input_items": runs[first_run_id]["item_count"],
                    "clustered_candidates": runs[first_run_id]["candidate_count"],
                    "fallback_used": runs[first_run_id]["fallback_used"],
                    "error_count": len(first_errors),
                },
                "review": {
                    "action": "approved",
                    "note": review_history[0]["note"],
                },
                "second_run": {
                    "run_id": second_run_id,
                    "mode": "live",
                    "source_stats": _source_summary(runs[second_run_id]),
                    "input_items": runs[second_run_id]["item_count"],
                    "clustered_candidates": runs[second_run_id]["candidate_count"],
                    "fallback_used": runs[second_run_id]["fallback_used"],
                    "error_count": len(second_errors),
                },
                "candidate_before_review_feedback": before,
                "candidate_after_review_feedback": after,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
