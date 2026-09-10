from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

from .config import DEFAULT_PROFILE, get_ai_settings


CHAT_TASK_STATUSES = {
    "awaiting_confirmation",
    "queued",
    "running",
    "succeeded",
    "partial_success",
    "failed",
}
OUTPUT_MODES = {"raw_data", "summary", "candidate_pool"}
SOURCE_MODES = {"demo", "live", "domestic"}

DEFAULT_CHAT_PROFILE = {
    "audience": "英语市场的 AI 用户、创作者和产品团队",
    "focus_topics": "AI products, agents, developer tools, creator workflows",
    "avoid_topics": "",
    "tone": "简洁、专业、有明确观点，不过度宣传",
    "free_text": DEFAULT_PROFILE,
}


@dataclass(frozen=True)
class ChatParseResult:
    plan: dict[str, object]
    used_model: bool
    fallback_notice: str = ""


def _text(value: object, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _parse_json(content: str) -> dict[str, object]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("模型返回的任务计划不是 JSON 对象")
    return payload


def _normalize_profile(value: object) -> dict[str, str]:
    profile = dict(DEFAULT_CHAT_PROFILE)
    if isinstance(value, Mapping):
        for key in profile:
            if value.get(key) is not None:
                profile[key] = _text(value[key], 800)
    elif value:
        profile["free_text"] = _text(value, 800)
    return profile


def _normalize_plan(payload: Mapping[str, object], message: str, profile: object = None) -> dict[str, object]:
    source_mode = _text(payload.get("source_mode") or payload.get("mode"), 30).lower()
    if source_mode not in SOURCE_MODES:
        source_mode = "demo"

    output_mode = _text(payload.get("output_mode") or payload.get("output"), 40).lower()
    aliases = {
        "raw": "raw_data", "data": "raw_data", "原始数据": "raw_data",
        "summary": "summary", "摘要": "summary", "报告": "summary",
        "candidate": "candidate_pool", "candidates": "candidate_pool",
        "候选": "candidate_pool", "候选池": "candidate_pool",
    }
    output_mode = aliases.get(output_mode, output_mode)
    if output_mode not in OUTPUT_MODES:
        output_mode = "candidate_pool"

    time_range = _text(payload.get("time_range") or payload.get("time_window"), 60)
    if not time_range:
        time_range = "last_24_hours"
    query = _text(payload.get("query") or payload.get("topic") or message, 1000)
    if not query:
        raise ValueError("聊天需求不能为空")

    return {
        "query": query,
        "source_mode": source_mode,
        "time_range": time_range,
        "output_mode": output_mode,
        "profile": _normalize_profile(payload.get("profile") or profile),
        "needs_confirmation": True,
    }


def _fallback_plan(message: str, profile: object = None) -> dict[str, object]:
    lowered = message.lower()
    source_mode = "demo"
    if any(token in message for token in ("国内", "中文", "36氪", "少数派")):
        source_mode = "domestic"
    elif any(token in message for token in ("海外", "国外", "国际", "真实", "rss", "atom")):
        source_mode = "live"

    if any(token in message for token in ("只抓", "原始数据", "原始素材", "只要数据")):
        output_mode = "raw_data"
    elif any(token in message for token in ("摘要", "报告", "总结")):
        output_mode = "summary"
    else:
        output_mode = "candidate_pool"

    time_range = "last_24_hours"
    if "一周" in message or "7天" in message or "最近一周" in message:
        time_range = "last_7_days"
    elif "48小时" in message:
        time_range = "last_48_hours"

    query = message.strip()
    if not query and lowered:
        query = lowered
    return _normalize_plan(
        {
            "query": query,
            "source_mode": source_mode,
            "time_range": time_range,
            "output_mode": output_mode,
            "profile": profile,
        },
        message,
        profile,
    )


def parse_chat_request(
    message: str,
    profile: object = None,
    settings: Mapping[str, object] | None = None,
) -> ChatParseResult:
    """Parse a natural-language demo request with a validated model/fallback plan."""
    normalized = _text(message, 2000)
    if not normalized:
        raise ValueError("请输入需要抓取或整理的需求")
    active = dict(settings) if settings is not None else get_ai_settings()
    if active.get("enabled", True) and active.get("api_key"):
        try:
            from openai import OpenAI

            client_kwargs = {"api_key": str(active["api_key"])}
            if active.get("base_url"):
                client_kwargs["base_url"] = str(active["base_url"])
            client = OpenAI(**client_kwargs, timeout=30.0, max_retries=1)
            response = client.chat.completions.create(
                model=str(active["model"]),
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a task planner for a local hotspot content demo. "
                            "Return JSON only with keys query, source_mode, time_range, output_mode, profile. "
                            "source_mode must be demo, live, or domestic. "
                            "output_mode must be raw_data, summary, or candidate_pool. "
                            "Use demo when the user does not explicitly request live or domestic sources. "
                            "Never invent a provider, URL, API key, or execution result."
                        ),
                    },
                    {"role": "user", "content": normalized},
                ],
            )
            content = response.choices[0].message.content or "{}"
            plan = _normalize_plan(_parse_json(content), normalized, profile)
            return ChatParseResult(plan=plan, used_model=True)
        except Exception as exc:
            return ChatParseResult(
                plan=_fallback_plan(normalized, profile),
                used_model=False,
                fallback_notice=f"模型暂时不可用（{type(exc).__name__}），已使用规则解析并切换演示快照。",
            )

    return ChatParseResult(
        plan=_fallback_plan(normalized, profile),
        used_model=False,
        fallback_notice="未配置可用模型 API，已使用规则解析；演示执行将使用固定快照。",
    )


def plan_summary(plan: Mapping[str, object]) -> str:
    source_labels = {"demo": "演示快照", "live": "海外 RSS / Atom", "domestic": "国内 RSS / Atom"}
    output_labels = {
        "raw_data": "原始数据",
        "summary": "摘要报告",
        "candidate_pool": "候选内容池",
    }
    return (
        f"我理解为：抓取“{_text(plan.get('query'), 180)}”，时间范围为 {plan.get('time_range')}，"
        f"使用{source_labels.get(str(plan.get('source_mode')), '演示快照')}，输出{output_labels.get(str(plan.get('output_mode')), '候选内容池')}。"
        "确认后开始执行。"
    )
