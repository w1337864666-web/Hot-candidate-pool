from __future__ import annotations

import time

from .ai import AI_REVIEW_NOT_EVALUATED, enrich_candidate
from .config import DEFAULT_PROFILE, ai_configured, get_ai_settings, get_api_settings
from .content import ContentResult, enrich_items
from .core import AccountProfile, Candidate, FeedbackProfile, analyze_items
from .data_api import TAVILY_SOURCE_NAME, fetch_supplemental_news
from .sources import DEFAULT_SOURCES, SourceConfig, demo_items, fetch_all_with_stats
from .providers import RSSProvider, TavilyProvider
from .storage import Store


def run_scan(
    mode: str,
    profile: AccountProfile | str | None = None,
    store: Store | None = None,
    configs: list[SourceConfig] | None = None,
    trigger_type: str = "manual",
    persist_candidates: bool = True,
) -> tuple[int, list[str], list[Candidate]]:
    started = time.perf_counter()
    if profile is None:
        account_profile = AccountProfile.from_form(free_text=DEFAULT_PROFILE)
    elif isinstance(profile, AccountProfile):
        account_profile = profile
    else:
        account_profile = AccountProfile.from_form(free_text=profile)
    configs = list(configs or DEFAULT_SOURCES)
    rss_provider = RSSProvider(fetch_all_with_stats)
    tavily_provider = TavilyProvider(fetch_supplemental_news)
    owned_store = store is None
    active_store = store or Store()
    stored_settings = active_store.app_settings()
    api_settings = get_api_settings(stored_settings)
    ai_settings = get_ai_settings(stored_settings)
    if mode == "demo":
        items, errors = demo_items(), []
        source_stats = [
            {
                "source_type": config.source_type,
                "source_name": config.source_name,
                "url": config.url,
                "status": "demo",
                "attempts": 0,
                "item_count": sum(1 for item in items if item.source_name == config.source_name),
                "success_count": sum(1 for item in items if item.source_name == config.source_name),
                "failure_count": 0,
                "content_success_count": 0,
                "content_failure_count": 0,
                "content_skipped_count": 0,
                "fallback_count": 0,
                "duration_ms": 0,
                "error": "",
                "fallback_used": False,
            }
            for config in configs
        ]
        fallback_used = False
    else:
        rss_result = rss_provider.fetch(configs)
        items, errors, source_stats = rss_result.items, rss_result.errors, rss_result.stats or []
        tavily_result = tavily_provider.fetch(api_settings, account_profile, mode)
        supplemental_items = tavily_result.items
        supplemental_error = tavily_result.errors[0] if tavily_result.errors else None
        supplemental_stats = tavily_result.stats if isinstance(tavily_result.stats, dict) else None
        items.extend(supplemental_items)
        if supplemental_error:
            errors.append(supplemental_error)
        if supplemental_stats:
            source_stats.append(supplemental_stats)
        fallback_used = False
        if not items:
            items = demo_items()
            errors.append("公开 RSS/Atom 暂时无法访问，已切换为演示快照。")
            fallback_used = True
            for source_stats_item in source_stats:
                source_stats_item["fallback_used"] = True
                source_stats_item["status"] = "fallback"
                source_stats_item["item_count"] = sum(
                    1 for item in items if item.source_name == source_stats_item["source_name"]
                )
                source_stats_item["fallback_count"] = source_stats_item["item_count"]

        if not fallback_used:
            cached_rows = active_store.article_content_cache(item.url for item in items)
            cache = {
                url: ContentResult(
                    content=str(row["content"] or ""),
                    status="extracted" if row["content_status"] == "extracted" else "failed",
                    source=str(row["content_source"] or "rss_summary"),
                    error=str(row["content_error"] or ""),
                    fetched_at=str(row["content_fetched_at"] or ""),
                )
                for url, row in cached_rows.items()
            }
            items, content_errors = enrich_items(items, cache=cache)
            errors.extend(content_errors)
            for source_stats_item in source_stats:
                if source_stats_item["source_name"] == TAVILY_SOURCE_NAME:
                    source_items = [item for item in items if item.source_type == "数据检索"]
                else:
                    source_items = [item for item in items if item.source_name == source_stats_item["source_name"]]
                source_stats_item["content_success_count"] = sum(
                    1 for item in source_items if item.content_status == "extracted"
                )
                source_stats_item["content_failure_count"] = sum(
                    1 for item in source_items if item.content_status == "summary_fallback"
                )
                source_stats_item["content_skipped_count"] = sum(
                    1 for item in source_items if item.content_status == "skipped"
                )
    feedback = FeedbackProfile.from_signals(
        active_store.feedback_signals(),
        topics=(*account_profile.focus_topics, *account_profile.avoid_topics),
    )
    candidates = analyze_items(items, profile=account_profile, feedback=feedback)
    if ai_configured(ai_settings):
        candidate_limit = int(ai_settings.get("candidate_limit", 12))
        if ai_settings.get("review_enabled", True):
            for candidate in candidates[candidate_limit:]:
                candidate.ai_review_status = AI_REVIEW_NOT_EVALUATED
                candidate.ai_review_reason = "本轮未进入模型评估额度，不进入候选一。"
                candidate.ai_review_confidence = 0
        for candidate in candidates[:candidate_limit]:
            enrich_candidate(candidate, account_profile.as_prompt_text(), settings=ai_settings)
        candidates.sort(key=lambda candidate: candidate.event.priority_score, reverse=True)
    try:
        run_id = active_store.save_run(
            mode,
            configs,
            items,
            candidates if persist_candidates else [],
            errors,
            profile=account_profile,
            source_stats=source_stats,
            duration_ms=round((time.perf_counter() - started) * 1000),
            fallback_used=fallback_used,
            trigger_type=trigger_type,
        )
    finally:
        if owned_store:
            active_store.close()
    return run_id, errors, candidates
