import unittest
from datetime import datetime, timezone

from hotspot_agent.core import (
    AccountProfile,
    FeedbackProfile,
    FeedbackSignal,
    SourceItem,
    analyze_items,
    assess_item_quality,
    calculate_heat_score,
    cluster_items,
)


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = [
            SourceItem("媒体新闻", "Media", "AI agents enter everyday work tools", "AI agents are becoming useful at work.", "https://example.com/a", "2026-08-20T08:00:00+00:00"),
            SourceItem("官方公告", "Official", "AI agents enter everyday work tools", "A product announcement focuses on AI workflows.", "https://example.com/b", "2026-08-20T08:10:00+00:00"),
            SourceItem("社区趋势", "Community", "Different unrelated topic", "A topic about cooking.", "https://example.com/c", "2026-08-20T08:20:00+00:00"),
        ]

    def test_related_items_are_clustered(self) -> None:
        clusters = cluster_items(self.items)
        self.assertEqual(sorted(len(cluster) for cluster in clusters), [1, 2])

    def test_same_source_boilerplate_does_not_merge_unrelated_titles(self) -> None:
        items = [
            SourceItem(
                "社区趋势", "Hacker News AI", "AI coding changes developer habits",
                "A Hacker News discussion links to example.com about this story. It currently has 1 point and 0 comments.",
                "https://example.com/one", "2026-08-20T08:00:00+00:00",
            ),
            SourceItem(
                "社区趋势", "Hacker News AI", "Robotics startup opens a new research lab",
                "A Hacker News discussion links to example.org about this story. It currently has 1 point and 0 comments.",
                "https://example.org/two", "2026-08-20T08:00:00+00:00",
            ),
        ]
        self.assertEqual(len(cluster_items(items)), 2)

    def test_analysis_returns_sorted_candidates_with_sources(self) -> None:
        candidates = analyze_items(self.items, now=datetime(2026, 8, 20, 10, tzinfo=timezone.utc))
        self.assertEqual(len(candidates), 2)
        self.assertGreaterEqual(candidates[0].event.priority_score, candidates[1].event.priority_score)
        self.assertTrue(candidates[0].event.items)
        self.assertIn("https://", candidates[0].event.items[0].url)

    def test_deterministic_x_copy_is_readable_and_sized(self) -> None:
        item = SourceItem(
            "社区趋势",
            "Hacker News AI",
            "Show HN: A practical AI workflow for product teams",
            "Article URL: https://example.com/story Comments URL: https://news.ycombinator.com/item?id=1 Points: 4 # Comments: 2",
            "https://example.com/story",
            "2026-08-20T08:00:00+00:00",
        )
        candidate = analyze_items([item], now=datetime(2026, 8, 20, 10, tzinfo=timezone.utc))[0]
        self.assertLessEqual(len(candidate.english_copy), 280)
        self.assertIn("Why it matters:", candidate.english_copy)
        self.assertNotIn("Article URL:", candidate.event.summary)
        self.assertNotIn("Points:", candidate.english_copy)

    def test_single_source_rumor_is_flagged(self) -> None:
        item = SourceItem("媒体新闻", "Media", "AI rumor spreads", "An alleged model launch rumor.", "https://example.com/r", "2026-08-20T08:00:00+00:00")
        candidate = analyze_items([item], now=datetime(2026, 8, 20, 10, tzinfo=timezone.utc))[0]
        self.assertIn("单一来源，建议核验", candidate.event.risk_flags)
        self.assertIn("可能存在未经证实信息", candidate.event.risk_flags)

    def test_ai_keyword_does_not_match_inside_unrelated_words(self) -> None:
        item = SourceItem(
            "媒体新闻", "Media", "Venture capital board investigation",
            "The report covers investors, company boards, and antitrust law.",
            "https://example.com/capital", "2026-08-20T08:00:00+00:00",
        )
        candidate = analyze_items([item], now=datetime(2026, 8, 20, 10, tzinfo=timezone.utc))[0]
        self.assertEqual(candidate.event.follow_decision, "暂不跟进")
        self.assertEqual(candidate.event.base_priority_score, 47)
        self.assertEqual(candidate.event.credibility_score, 80)
        self.assertEqual(candidate.event.relevance_score, 10)
        self.assertEqual(candidate.event.recommendation, "不推荐")

    def test_profile_focus_and_avoid_topics_adjust_score(self) -> None:
        focus_profile = AccountProfile.from_form(focus_topics="agents")
        focused = analyze_items(self.items, now=datetime(2026, 8, 20, 10, tzinfo=timezone.utc), profile=focus_profile)[0]
        self.assertGreater(focused.event.profile_adjustment, 0)
        self.assertIn("命中关注主题", focused.event.follow_reason)

        avoid_profile = AccountProfile.from_form(avoid_topics="work tools")
        avoided = next(
            candidate
            for candidate in analyze_items(self.items, now=datetime(2026, 8, 20, 10, tzinfo=timezone.utc), profile=avoid_profile)
            if "agents" in candidate.event.title.lower()
        )
        self.assertLess(avoided.event.profile_adjustment, 0)
        self.assertIn("命中账号回避主题", avoided.event.risk_flags)

    def test_feedback_adjustment_is_bounded_and_explained(self) -> None:
        feedback = FeedbackProfile.from_signals([
            FeedbackSignal("approved", "AI agents enter everyday work tools", "AI agents are useful", ("媒体新闻",), "2026-08-20T09:00:00+00:00"),
            FeedbackSignal("rejected", "AI agents enter everyday work tools", "AI agents are useful", ("媒体新闻",), "2026-08-20T09:00:00+00:00"),
        ])
        candidate = analyze_items(self.items, now=datetime(2026, 8, 20, 10, tzinfo=timezone.utc), feedback=feedback)[0]
        self.assertNotEqual(candidate.event.feedback_adjustment, 0)
        self.assertLessEqual(abs(candidate.event.feedback_adjustment), 12)
        self.assertIn("历史审核", candidate.event.follow_reason)

    def test_feedback_tracks_custom_profile_topics(self) -> None:
        feedback = FeedbackProfile.from_signals(
            [FeedbackSignal("approved", "Security update", "Security controls improve", ("官方公告",), "2026-08-20T09:00:00+00:00")],
            topics=("security",),
        )
        item = SourceItem("官方公告", "Official", "Security update", "Security controls improve", "https://example.com/security", "2026-08-20T08:00:00+00:00")
        candidate = analyze_items([item], now=datetime(2026, 8, 20, 10, tzinfo=timezone.utc), feedback=feedback)[0]
        self.assertGreater(candidate.event.feedback_adjustment, 0)
        self.assertIn("历史审核主题调整", candidate.event.follow_reason)

    def test_quality_is_explained_without_changing_score_components(self) -> None:
        item = SourceItem(
            "官方公告", "Official", "AI product release with context",
            "A concise feed summary.", "https://example.com/quality",
            "2026-08-20T08:00:00+00:00",
            content="A detailed article body " * 40,
            content_status="extracted",
            content_source="article",
        )
        candidate = analyze_items([item], now=datetime(2026, 8, 20, 10, tzinfo=timezone.utc))[0]
        self.assertGreaterEqual(candidate.event.quality_score, 80)
        self.assertIn("内容质量评估", candidate.event.follow_reason)
        self.assertEqual(candidate.event.priority_score, candidate.event.base_priority_score)

    def test_real_engagement_heat_increases_base_priority(self) -> None:
        common = {
            "source_type": "社区趋势",
            "source_name": "Hacker News AI",
            "title": "AI agent evaluation workflow",
            "summary": "Developers discuss AI agent evaluation.",
            "published_at": "2026-08-20T08:00:00+00:00",
        }
        cold = SourceItem(url="https://example.com/cold", **common)
        hot = SourceItem(
            url="https://example.com/hot",
            engagement_points=220,
            engagement_comments=85,
            heat_score=calculate_heat_score(220, 85),
            **common,
        )
        current = datetime(2026, 8, 20, 10, tzinfo=timezone.utc)
        cold_event = analyze_items([cold], now=current)[0].event
        hot_event = analyze_items([hot], now=current)[0].event
        self.assertGreater(hot_event.heat_score, 0)
        self.assertGreater(hot_event.base_priority_score, cold_event.base_priority_score)

    def test_source_credibility_directly_changes_base_priority(self) -> None:
        values = {
            "source_name": "Source",
            "title": "AI model workflow update",
            "summary": "An AI product workflow update.",
            "url": "https://example.com/update",
            "published_at": "2026-08-20T08:00:00+00:00",
        }
        official = SourceItem(source_type="官方公告", **values)
        community = SourceItem(source_type="社区趋势", **values)
        current = datetime(2026, 8, 20, 10, tzinfo=timezone.utc)
        official_event = analyze_items([official], now=current)[0].event
        community_event = analyze_items([community], now=current)[0].event
        self.assertGreater(official_event.credibility_score, community_event.credibility_score)
        self.assertGreater(official_event.base_priority_score, community_event.base_priority_score)

    def test_rule_relevance_outputs_binary_recommendation_without_model(self) -> None:
        profile = AccountProfile.from_form(focus_topics="agents", avoid_topics="rumors")
        recommended = analyze_items(
            [self.items[0]],
            now=datetime(2026, 8, 20, 10, tzinfo=timezone.utc),
            profile=profile,
        )[0].event
        blocked = analyze_items(
            [SourceItem(
                "媒体新闻", "Media", "AI agents rumor",
                "An unverified AI agents report.", "https://example.com/rumor",
                "2026-08-20T08:00:00+00:00",
            )],
            now=datetime(2026, 8, 20, 10, tzinfo=timezone.utc),
            profile=profile,
        )[0].event
        self.assertEqual(recommended.recommendation, "推荐")
        self.assertGreaterEqual(recommended.relevance_score, 45)
        self.assertEqual(blocked.recommendation, "不推荐")
        self.assertIn("未经证实", blocked.recommendation_reason)
