from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


DEFAULT_PROFILE = "海外 AI 产品官方账号，面向英语市场的 AI 用户、创作者和产品团队。"
DEFAULT_MODEL_CANDIDATE_LIMIT = 12
DEFAULT_MODEL_REVIEW_THRESHOLD = 70
DEFAULT_SCHEDULE_TIME = "09:00"
DEFAULT_SCHEDULE_MODE = "live"


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: object, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


def load_local_env(path: str | Path = ".env") -> None:
    """Load simple KEY=VALUE pairs without requiring a dotenv dependency."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_api_settings(overrides: Mapping[str, str] | None = None) -> dict[str, object]:
    load_local_env()
    values: dict[str, object] = {
        "model_api_key": os.getenv("OPENAI_API_KEY", ""),
        "model_base_url": os.getenv("OPENAI_BASE_URL", ""),
        "model_name": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "model_enabled": os.getenv("MODEL_API_ENABLED", "1"),
        "model_scoring_enabled": os.getenv("MODEL_SCORING_ENABLED", "1"),
        "model_content_enabled": os.getenv("MODEL_CONTENT_ENABLED", "1"),
        "model_review_enabled": os.getenv("MODEL_REVIEW_ENABLED", "1"),
        "model_candidate_limit": os.getenv("MODEL_CANDIDATE_LIMIT", str(DEFAULT_MODEL_CANDIDATE_LIMIT)),
        "model_review_threshold": os.getenv(
            "MODEL_REVIEW_THRESHOLD", str(DEFAULT_MODEL_REVIEW_THRESHOLD)
        ),
        "data_api_key": os.getenv("TAVILY_API_KEY", ""),
        "data_api_enabled": os.getenv("DATA_API_ENABLED", "1"),
    }
    for key, value in (overrides or {}).items():
        if key in values:
            values[key] = value
    return {
        "model_api_key": str(values["model_api_key"]),
        "model_base_url": str(values["model_base_url"]),
        "model_name": str(values["model_name"] or "gpt-4o-mini"),
        "model_enabled": _as_bool(values["model_enabled"], True),
        "model_scoring_enabled": _as_bool(values["model_scoring_enabled"], True),
        "model_content_enabled": _as_bool(values["model_content_enabled"], True),
        "model_review_enabled": _as_bool(values["model_review_enabled"], True),
        "model_candidate_limit": _as_int(
            values["model_candidate_limit"], DEFAULT_MODEL_CANDIDATE_LIMIT, 1, 20
        ),
        "model_review_threshold": _as_int(
            values["model_review_threshold"], DEFAULT_MODEL_REVIEW_THRESHOLD, 50, 95
        ),
        "data_api_key": str(values["data_api_key"]),
        "data_api_enabled": _as_bool(values["data_api_enabled"], True),
    }


def get_ai_settings(overrides: Mapping[str, str] | None = None) -> dict[str, object]:
    settings = get_api_settings(overrides)
    return {
        "api_key": settings["model_api_key"],
        "base_url": settings["model_base_url"],
        "model": settings["model_name"],
        "enabled": settings["model_enabled"],
        "scoring_enabled": settings["model_scoring_enabled"],
        "content_enabled": settings["model_content_enabled"],
        "review_enabled": settings["model_review_enabled"],
        "candidate_limit": settings["model_candidate_limit"],
        "review_threshold": settings["model_review_threshold"],
    }


def get_schedule_settings(overrides: Mapping[str, str] | None = None) -> dict[str, object]:
    load_local_env()
    values: dict[str, object] = {
        "schedule_enabled": os.getenv("SCHEDULE_ENABLED", "0"),
        "schedule_time": os.getenv("SCHEDULE_TIME", DEFAULT_SCHEDULE_TIME),
        "schedule_mode": os.getenv("SCHEDULE_MODE", DEFAULT_SCHEDULE_MODE),
    }
    for key, value in (overrides or {}).items():
        if key in values:
            values[key] = value
    schedule_time = str(values["schedule_time"] or DEFAULT_SCHEDULE_TIME).strip()
    try:
        hour, minute = (int(part) for part in schedule_time.split(":"))
    except (TypeError, ValueError):
        schedule_time = DEFAULT_SCHEDULE_TIME
    else:
        if len(schedule_time) != 5 or not 0 <= hour <= 23 or not 0 <= minute <= 59:
            schedule_time = DEFAULT_SCHEDULE_TIME
    mode = str(values["schedule_mode"] or DEFAULT_SCHEDULE_MODE).strip()
    if mode not in {"demo", "live", "domestic"}:
        mode = DEFAULT_SCHEDULE_MODE
    return {
        "schedule_enabled": _as_bool(values["schedule_enabled"], False),
        "schedule_time": schedule_time,
        "schedule_mode": mode,
    }


def ai_configured(settings: Mapping[str, object] | None = None) -> bool:
    active = dict(settings) if settings is not None else get_ai_settings()
    return bool(
        active.get("enabled", True)
        and active.get("api_key")
        and (
            active.get("scoring_enabled", True)
            or active.get("content_enabled", True)
            or active.get("review_enabled", True)
        )
    )


def get_database_path() -> Path:
    return Path(os.getenv("HOTSPOT_DB_PATH", "data/hotspot_agent.db"))
