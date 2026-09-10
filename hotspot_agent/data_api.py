from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone
from typing import Callable, Mapping
from urllib.parse import urlparse

import httpx

from .core import AccountProfile, SourceItem


TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_SOURCE_NAME = "Tavily Search"


def _plain_text(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def _search_query(profile: AccountProfile, mode: str) -> str:
    topics = " ".join(profile.focus_topics) or "AI products agents developer tools"
    if mode == "domestic":
        return f"中国 人工智能 AI 产品 最新动态 {topics}"
    return f"latest AI product agent developer news {topics}"


def _request(
    url: str,
    payload: dict[str, object],
    headers: dict[str, str],
    timeout: float,
) -> httpx.Response:
    return httpx.post(url, json=payload, headers=headers, timeout=timeout, trust_env=True)


def fetch_supplemental_news(
    settings: Mapping[str, object],
    profile: AccountProfile,
    mode: str,
    *,
    timeout: float = 15,
    max_results: int = 10,
    request_fn: Callable[[str, dict[str, object], dict[str, str], float], httpx.Response] | None = None,
) -> tuple[list[SourceItem], str | None, dict[str, object] | None]:
    api_key = str(settings.get("data_api_key") or "").strip()
    if not settings.get("data_api_enabled", True) or not api_key:
        return [], None, None

    started = time.monotonic()
    request = request_fn or _request
    payload = {
        "query": _search_query(profile, mode),
        "search_depth": "basic",
        "max_results": max(1, min(20, int(max_results))),
        "topic": "news",
        "time_range": "week",
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "include_usage": True,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "daily-hotspot-content-agent/0.3",
    }
    try:
        response = request(TAVILY_SEARCH_URL, payload, headers, timeout)
        response.raise_for_status()
        data = response.json()
        raw_results = data.get("results") if isinstance(data, dict) else []
        items: list[SourceItem] = []
        for result in raw_results if isinstance(raw_results, list) else []:
            if not isinstance(result, dict):
                continue
            title = _plain_text(result.get("title"))
            url = str(result.get("url") or "").strip()
            summary = _plain_text(result.get("content"))
            if not title or not url or not summary:
                continue
            domain = urlparse(url).netloc.removeprefix("www.") or TAVILY_SOURCE_NAME
            published_at = str(result.get("published_date") or datetime.now(timezone.utc).isoformat())
            items.append(
                SourceItem(
                    source_type="数据检索",
                    source_name=f"{domain} · Tavily",
                    title=title,
                    summary=summary,
                    url=url,
                    published_at=published_at,
                )
            )
        if not items:
            raise ValueError("data API returned no usable results")
        return items, None, {
            "source_type": "数据检索",
            "source_name": TAVILY_SOURCE_NAME,
            "url": TAVILY_SEARCH_URL,
            "status": "ok",
            "attempts": 1,
            "item_count": len(items),
            "success_count": len(items),
            "failure_count": 0,
            "content_success_count": 0,
            "content_failure_count": 0,
            "content_skipped_count": 0,
            "fallback_count": 0,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "error": "",
            "fallback_used": False,
            "usage": data.get("usage", {}) if isinstance(data, dict) else {},
        }
    except Exception as exc:
        error = f"{TAVILY_SOURCE_NAME}: {exc}"
        return [], error, {
            "source_type": "数据检索",
            "source_name": TAVILY_SOURCE_NAME,
            "url": TAVILY_SEARCH_URL,
            "status": "failed",
            "attempts": 1,
            "item_count": 0,
            "success_count": 0,
            "failure_count": 1,
            "content_success_count": 0,
            "content_failure_count": 0,
            "content_skipped_count": 0,
            "fallback_count": 0,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "error": error,
            "fallback_used": False,
        }
