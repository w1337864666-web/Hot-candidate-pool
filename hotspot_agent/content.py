from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable, Iterable

import httpx

from .core import SourceItem


MAX_ARTICLES_PER_SOURCE = 5
MAX_CONTENT_WORKERS = 6


@dataclass(frozen=True)
class ContentResult:
    content: str
    status: str
    source: str
    error: str = ""
    fetched_at: str = ""


class _ArticleTextParser(HTMLParser):
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "aside"}
    _CONTENT_TAGS = {"article", "main", "p", "h1", "h2", "h3", "li"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._content_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._CONTENT_TAGS:
            self._content_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._CONTENT_TAGS and self._content_depth:
            self._content_depth -= 1
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and self._content_depth and data.strip():
            self._parts.append(data.strip())

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._parts)).strip()


def _request(url: str, timeout: float, headers: dict[str, str]) -> httpx.Response:
    return httpx.get(url, timeout=timeout, headers=headers, follow_redirects=True)


def extract_article(
    url: str,
    timeout: float = 8,
    retries: int = 1,
    backoff: float = 0.2,
    request_fn: Callable[[str, float, dict[str, str]], httpx.Response] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ContentResult:
    request = request_fn or _request
    headers = {"User-Agent": "daily-hotspot-content-agent/0.2"}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = request(url, timeout, headers)
            response.raise_for_status()
            parser = _ArticleTextParser()
            parser.feed(response.text)
            content = parser.text()
            if len(content) < 80:
                raise ValueError("article page returned insufficient readable text")
            return ContentResult(
                content=content[:12000],
                status="extracted",
                source="article",
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:  # article extraction is best effort
            last_error = exc
            if attempt < retries:
                sleep_fn(backoff * (2**attempt))
    return ContentResult(
        content="",
        status="failed",
        source="article",
        error=str(last_error or "unknown extraction error"),
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def apply_content_result(item: SourceItem, result: ContentResult) -> SourceItem:
    item.content = result.content or item.summary
    item.content_status = result.status if result.status in {"extracted", "skipped"} else "summary_fallback"
    item.content_source = result.source if result.status == "extracted" else "rss_summary"
    item.content_error = result.error
    item.content_fetched_at = result.fetched_at
    return item


def enrich_items(
    items: Iterable[SourceItem],
    cache: dict[str, ContentResult] | None = None,
    request_fn: Callable[[str, float, dict[str, str]], httpx.Response] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_per_source: int = MAX_ARTICLES_PER_SOURCE,
    max_workers: int = MAX_CONTENT_WORKERS,
) -> tuple[list[SourceItem], list[str]]:
    """Extract a bounded sample concurrently and preserve summaries on failure."""
    item_list = list(items)
    results = cache if cache is not None else {}
    selected_urls: dict[str, str] = {}
    selected_by_source: dict[str, int] = {}
    for item in item_list:
        if item.url in results or item.url in selected_urls:
            continue
        selected_count = selected_by_source.get(item.source_name, 0)
        if selected_count < max_per_source:
            selected_urls[item.url] = item.source_name
            selected_by_source[item.source_name] = selected_count + 1

    if selected_urls:
        worker_count = max(1, min(max_workers, len(selected_urls)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_urls = {
                executor.submit(extract_article, url, request_fn=request_fn, sleep_fn=sleep_fn): url
                for url in selected_urls
            }
            for future in as_completed(future_urls):
                url = future_urls[future]
                try:
                    results[url] = future.result()
                except Exception as exc:  # defensive isolation between article jobs
                    results[url] = ContentResult("", "failed", "article", str(exc))

    errors: list[str] = []
    for item in item_list:
        result = results.get(item.url)
        if result is None:
            result = ContentResult(
                "",
                "skipped",
                "rss_summary",
                "为控制扫描耗时，本轮保留 RSS 摘要。",
            )
        apply_content_result(item, result)
        if result.status == "failed" and result.error:
            errors.append(f"{item.source_name} 正文抽取失败：{result.error}")
    return item_list, list(dict.fromkeys(errors))
