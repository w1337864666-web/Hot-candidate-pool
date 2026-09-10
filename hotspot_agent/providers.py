from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from .core import AccountProfile, SourceItem
from .data_api import fetch_supplemental_news
from .sources import SourceConfig, fetch_all_with_stats


@dataclass(frozen=True)
class ProviderResult:
    items: list[SourceItem]
    errors: list[str]
    stats: dict[str, object] | list[dict[str, object]] | None = None


class DataProvider(Protocol):
    provider_name: str

    def fetch(self, *args: object, **kwargs: object) -> ProviderResult:
        ...


class RSSProvider:
    provider_name = "rss_atom"

    def __init__(self, fetcher: Callable[..., tuple[list[SourceItem], list[str], list[dict[str, object]]]] = fetch_all_with_stats) -> None:
        self.fetcher = fetcher

    def fetch(self, configs: Sequence[SourceConfig]) -> ProviderResult:
        items, errors, stats = self.fetcher(configs)
        return ProviderResult(items=items, errors=errors, stats=stats)


class TavilyProvider:
    provider_name = "tavily_search"

    def __init__(self, fetcher: Callable[..., tuple[list[SourceItem], str | None, dict[str, object] | None]] = fetch_supplemental_news) -> None:
        self.fetcher = fetcher

    def fetch(
        self,
        settings: dict[str, object],
        profile: AccountProfile,
        mode: str,
    ) -> ProviderResult:
        items, error, stats = self.fetcher(settings, profile, mode)
        return ProviderResult(items=items, errors=[error] if error else [], stats=stats)


def configured_data_providers() -> tuple[DataProvider, DataProvider]:
    """Return the demo's two explicit data boundaries without adding providers."""
    return RSSProvider(), TavilyProvider()
