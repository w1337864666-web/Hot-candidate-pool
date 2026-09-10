from __future__ import annotations

import hashlib
import html
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

import feedparser
import httpx

from .core import SourceItem, calculate_heat_score


@dataclass(frozen=True)
class SourceConfig:
    source_type: str
    source_name: str
    url: str


DEFAULT_SOURCES = [
    SourceConfig("媒体新闻", "TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    SourceConfig("官方公告", "OpenAI News", "https://openai.com/news/rss.xml"),
    SourceConfig("社区趋势", "Hacker News AI", "https://hnrss.org/newest?q=AI"),
]

# A separate domestic preset keeps the overseas sources available when the
# user needs the broader English-market signal, while providing an alternative
# for networks that cannot access those domains reliably.
DOMESTIC_SOURCES = [
    SourceConfig("媒体新闻", "36氪", "https://36kr.com/feed"),
    SourceConfig("媒体新闻", "少数派", "https://sspai.com/feed"),
    SourceConfig("媒体新闻", "IT之家", "https://www.ithome.com/rss/"),
]


def _published(entry: dict) -> str:
    value = entry.get("published") or entry.get("updated") or entry.get("created")
    if value:
        return str(value)
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _plain_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", html.unescape(value))
    return re.sub(r"\s+", " ", value).strip()


def _hacker_news_summary(
    title: str,
    raw_summary: str,
    entry_link: str,
) -> tuple[str, str, int | None, int | None]:
    """Turn HNRSS metadata into a readable sentence while retaining the article link."""
    plain = _plain_text(raw_summary)
    metadata_present = any(
        marker.lower() in plain.lower()
        for marker in ("Article URL:", "Comments URL:", "Points:", "# Comments:")
    )
    if not metadata_present:
        return plain, entry_link, None, None

    article_match = re.search(r"Article URL:\s*(https?://\S+)", plain, flags=re.IGNORECASE)
    points_match = re.search(r"\bPoints:\s*(\d+)", plain, flags=re.IGNORECASE)
    comments_match = re.search(r"#\s*Comments:\s*(\d+)", plain, flags=re.IGNORECASE)
    article_url = article_match.group(1).rstrip(".,);]") if article_match else entry_link
    domain = urlparse(article_url).netloc.removeprefix("www.")

    if domain:
        summary = f"A Hacker News discussion links to {domain} about this story."
    else:
        summary = f'A Hacker News discussion is forming around "{title}."'

    metrics: list[str] = []
    points = int(points_match.group(1)) if points_match else None
    comments = int(comments_match.group(1)) if comments_match else None
    if points is not None:
        metrics.append(f"{points} point" if points == 1 else f"{points} points")
    if comments is not None:
        metrics.append(f"{comments} comment" if comments == 1 else f"{comments} comments")
    if metrics:
        summary += f" It currently has {' and '.join(metrics)}."
    return summary, article_url, points, comments


def parse_feed(payload: bytes | str, config: SourceConfig, limit: int = 20) -> list[SourceItem]:
    parsed = feedparser.parse(payload)
    items: list[SourceItem] = []
    for entry in parsed.entries[:limit]:
        title = str(entry.get("title", "")).strip()
        content = entry.get("content") or []
        content_value = content[0].get("value", "") if isinstance(content, list) and content else ""
        raw_summary = str(entry.get("summary") or entry.get("description") or content_value).strip()
        summary = _plain_text(raw_summary)
        link = str(entry.get("link", "")).strip()
        points: int | None = None
        comments: int | None = None
        if config.source_name == "Hacker News AI":
            summary, link, points, comments = _hacker_news_summary(title, raw_summary, link)
        if title and link:
            item_id = hashlib.sha1(f"{config.source_name}|{title}|{link}".encode("utf-8")).hexdigest()[:16]
            items.append(SourceItem(
                config.source_type,
                config.source_name,
                title,
                summary,
                link,
                _published(entry),
                item_id,
                engagement_points=points,
                engagement_comments=comments,
                heat_score=calculate_heat_score(points, comments),
            ))
    return items


def _request(url: str, timeout: float, headers: dict[str, str]) -> httpx.Response:
    return httpx.get(url, timeout=timeout, headers=headers, follow_redirects=True, trust_env=True)


def fetch_feed(
    config: SourceConfig,
    timeout: float = 12,
    retries: int = 2,
    backoff: float = 0.2,
    request_fn: Callable[[str, float, dict[str, str]], httpx.Response] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[list[SourceItem], str | None]:
    items, error, _stats = fetch_feed_detailed(
        config,
        timeout=timeout,
        retries=retries,
        backoff=backoff,
        request_fn=request_fn,
        sleep_fn=sleep_fn,
    )
    return items, error


def fetch_feed_detailed(
    config: SourceConfig,
    timeout: float = 12,
    retries: int = 2,
    backoff: float = 0.2,
    request_fn: Callable[[str, float, dict[str, str]], httpx.Response] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[list[SourceItem], str | None, dict[str, object]]:
    request = request_fn or _request
    headers = {"User-Agent": "daily-hotspot-content-agent/0.2"}
    last_error: Exception | None = None
    started = time.monotonic()
    attempts = 0
    for attempt in range(retries + 1):
        attempts += 1
        try:
            response = request(config.url, timeout, headers)
            response.raise_for_status()
            items = parse_feed(response.content, config)
            if not items:
                raise ValueError("feed returned no usable entries")
            return items, None, {
                "source_type": config.source_type,
                "source_name": config.source_name,
                "url": config.url,
                "status": "ok",
                "attempts": attempts,
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
            }
        except Exception as exc:  # sources fail independently
            last_error = exc
            if attempt < retries:
                sleep_fn(backoff * (2**attempt))
    error = f"{config.source_name}: {last_error}"
    return [], error, {
        "source_type": config.source_type,
        "source_name": config.source_name,
        "url": config.url,
        "status": "failed",
        "attempts": attempts,
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


def fetch_all(
    configs: list[SourceConfig] | None = None,
    **fetch_kwargs,
) -> tuple[list[SourceItem], list[str]]:
    items, errors, _stats = fetch_all_with_stats(configs, **fetch_kwargs)
    return items, errors


def fetch_all_with_stats(
    configs: list[SourceConfig] | None = None,
    **fetch_kwargs,
) -> tuple[list[SourceItem], list[str], list[dict[str, object]]]:
    items: list[SourceItem] = []
    errors: list[str] = []
    stats: list[dict[str, object]] = []
    for config in configs or DEFAULT_SOURCES:
        source_items, error, source_stats = fetch_feed_detailed(config, **fetch_kwargs)
        items.extend(source_items)
        stats.append(source_stats)
        if error:
            errors.append(error)
    return items, errors, stats


def demo_items() -> list[SourceItem]:
    """Return stable public-link fixtures for an offline interview demo."""
    published = "2026-08-20T08:00:00+00:00"
    return [
        SourceItem("媒体新闻", "TechCrunch AI", "AI assistants move into everyday work tools", "New product updates show a push toward assistants that complete multi-step work.", "https://techcrunch.com/category/artificial-intelligence/", published),
        SourceItem("官方公告", "OpenAI News", "OpenAI shares updates for developers building with AI models", "The announcement focuses on practical model capabilities and product workflows.", "https://openai.com/news/", published),
        SourceItem("社区趋势", "Hacker News AI", "Developers discuss reliable AI agents and human review loops", "Community discussion centers on evaluation, reliability, and human control.", "https://news.ycombinator.com/", published),
        SourceItem("媒体新闻", "TechCrunch AI", "AI assistants move from chat windows into work tools", "Coverage highlights assistants embedded in real workflows.", "https://techcrunch.com/category/artificial-intelligence/", published),
        SourceItem("社区趋势", "Hacker News AI", "The challenge for AI agents is proving trustworthy actions", "Developers debate evaluation and approval patterns for systems that act for users.", "https://news.ycombinator.com/", published),
    ]
