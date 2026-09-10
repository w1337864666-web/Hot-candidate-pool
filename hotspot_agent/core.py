from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Sequence


AI_KEYWORDS = {
    "ai",
    "artificial intelligence",
    "agent",
    "model",
    "llm",
    "generative",
    "openai",
    "anthropic",
    "google",
    "deepmind",
    "copilot",
    "automation",
    "creator",
    "productivity",
}
QUALITY_SOURCE_SCORES = {
    "官方公告": 88,
    "媒体新闻": 78,
    "社区趋势": 68,
    "数据检索": 60,
}
SOURCE_CREDIBILITY_SCORES = {
    "官方公告": 92,
    "媒体新闻": 80,
    "社区趋势": 65,
    "数据检索": 60,
}
FEEDBACK_TOPICS = tuple(sorted(AI_KEYWORDS))
FEEDBACK_WEIGHTS = {"approved": 2.0, "edited": 0.75, "rejected": -3.0}
MAX_PROFILE_ADJUSTMENT = 12
MAX_FEEDBACK_ADJUSTMENT = 12
MAX_MODEL_ADJUSTMENT = 10


def _split_topics(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    values = value.replace("，", ",").replace("\n", ",").split(",") if isinstance(value, str) else value
    return tuple(dict.fromkeys(str(item).strip().lower() for item in values if str(item).strip()))


def _topic_matches(topic: str, text: str) -> bool:
    normalized = re.sub(r"\s+", " ", topic.lower()).strip()
    if not normalized:
        return False
    pattern = re.escape(normalized).replace(r"\ ", r"\s+")
    return bool(re.search(rf"(?<![a-z0-9]){pattern}s?(?![a-z0-9])", text.lower()))


@dataclass(frozen=True)
class AccountProfile:
    audience: str = ""
    focus_topics: tuple[str, ...] = ()
    avoid_topics: tuple[str, ...] = ()
    tone: str = ""
    free_text: str = ""

    @classmethod
    def from_form(
        cls,
        audience: str = "",
        focus_topics: str | Sequence[str] | None = None,
        avoid_topics: str | Sequence[str] | None = None,
        tone: str = "",
        free_text: str = "",
    ) -> "AccountProfile":
        return cls(
            audience=audience.strip(),
            focus_topics=_split_topics(focus_topics),
            avoid_topics=_split_topics(avoid_topics),
            tone=tone.strip(),
            free_text=free_text.strip(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "audience": self.audience,
            "focus_topics": list(self.focus_topics),
            "avoid_topics": list(self.avoid_topics),
            "tone": self.tone,
            "free_text": self.free_text,
        }

    def as_prompt_text(self) -> str:
        parts = [
            f"Audience: {self.audience}" if self.audience else "",
            f"Focus topics: {', '.join(self.focus_topics)}" if self.focus_topics else "",
            f"Avoid topics: {', '.join(self.avoid_topics)}" if self.avoid_topics else "",
            f"Tone: {self.tone}" if self.tone else "",
            self.free_text,
        ]
        return "\n".join(part for part in parts if part)


@dataclass(frozen=True)
class FeedbackSignal:
    action: str
    title: str
    summary: str
    source_types: tuple[str, ...] = ()
    created_at: str = ""


@dataclass
class FeedbackProfile:
    topic_adjustments: dict[str, float] = field(default_factory=dict)
    source_adjustments: dict[str, float] = field(default_factory=dict)
    review_count: int = 0

    @classmethod
    def from_signals(
        cls,
        signals: Iterable[FeedbackSignal],
        now: datetime | None = None,
        topics: Iterable[str] | None = None,
    ) -> "FeedbackProfile":
        profile = cls()
        current = now or datetime.now(timezone.utc)
        tracked_topics = tuple(
            dict.fromkeys(
                (*FEEDBACK_TOPICS, *(_split_topics(tuple(topics)) if topics is not None else ()))
            )
        )
        for signal in signals:
            weight = FEEDBACK_WEIGHTS.get(signal.action)
            if weight is None:
                continue
            profile.review_count += 1
            age_factor = _feedback_age_factor(signal.created_at, current)
            weighted = weight * age_factor
            combined = f"{signal.title} {signal.summary}".lower()
            for topic in tracked_topics:
                if _topic_matches(topic, combined):
                    profile.topic_adjustments[topic] = _bounded(
                        profile.topic_adjustments.get(topic, 0.0) + weighted,
                        -6.0,
                        6.0,
                    )
            for source_type in set(signal.source_types):
                profile.source_adjustments[source_type] = _bounded(
                    profile.source_adjustments.get(source_type, 0.0) + weighted,
                    -6.0,
                    6.0,
                )
        return profile

    def adjustment_for(self, items: Sequence["SourceItem"]) -> tuple[int, list[str]]:
        combined = " ".join(f"{item.title} {item.summary}" for item in items).lower()
        topic_score = sum(value for topic, value in self.topic_adjustments.items() if _topic_matches(topic, combined))
        source_score = sum(self.source_adjustments.get(source_type, 0.0) for source_type in {item.source_type for item in items})
        adjustment = int(round(_bounded(topic_score + source_score, -MAX_FEEDBACK_ADJUSTMENT, MAX_FEEDBACK_ADJUSTMENT)))
        reasons: list[str] = []
        if topic_score:
            reasons.append(f"历史审核主题调整 {int(round(topic_score)):+d}")
        if source_score:
            reasons.append(f"历史审核来源调整 {int(round(source_score)):+d}")
        return adjustment, reasons


@dataclass
class SourceItem:
    source_type: str
    source_name: str
    title: str
    summary: str
    url: str
    published_at: str
    item_id: str = ""
    content: str = ""
    content_status: str = "rss_summary"
    content_source: str = "rss_summary"
    content_error: str = ""
    content_fetched_at: str = ""
    quality_score: int = 0
    quality_flags: list[str] = field(default_factory=list)
    engagement_points: int | None = None
    engagement_comments: int | None = None
    heat_score: int = 0

    def __post_init__(self) -> None:
        if not self.item_id:
            key = f"{self.source_name}|{self.title}|{self.url}"
            self.item_id = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        if not self.content:
            self.content = self.summary
        if not self.content_source:
            self.content_source = "rss_summary"


@dataclass
class Event:
    event_id: str
    title: str
    summary: str
    items: list[SourceItem] = field(default_factory=list)
    priority_score: int = 0
    priority_label: str = "观察"
    follow_decision: str = "建议评估"
    follow_reason: str = ""
    risk_flags: list[str] = field(default_factory=list)
    base_priority_score: int = 0
    profile_adjustment: int = 0
    feedback_adjustment: int = 0
    model_adjustment: int = 0
    model_reason: str = ""
    model_confidence: int = 0
    quality_score: int = 0
    quality_flags: list[str] = field(default_factory=list)
    heat_score: int = 0
    credibility_score: int = 0
    relevance_score: int = 0
    recommendation: str = "不推荐"
    recommendation_reason: str = ""


@dataclass
class Candidate:
    candidate_id: str
    event: Event
    content_angle: str
    english_copy: str
    status: str = "pending"
    ai_status: str = "template"
    optimized_title: str = ""
    optimized_summary: str = ""
    ai_review_status: str = "fallback"
    ai_review_reason: str = "未配置或未启用智能审核，当前使用确定性规则初筛。"
    ai_review_confidence: int = 0


def _bounded(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def calculate_heat_score(points: int | None, comments: int | None) -> int:
    """Normalize real community interactions without inventing missing signals."""
    if points is None and comments is None:
        return 0
    safe_points = max(0, int(points or 0))
    safe_comments = max(0, int(comments or 0))
    point_score = min(65.0, math.log1p(safe_points) / math.log1p(500) * 65)
    comment_score = min(35.0, math.log1p(safe_comments) / math.log1p(200) * 35)
    return int(round(_bounded(point_score + comment_score, 0, 100)))


def apply_model_assessment(
    event: Event,
    adjustment: object,
    reason: object,
    confidence: object,
) -> Event:
    try:
        bounded_adjustment = int(round(float(adjustment)))
    except (TypeError, ValueError):
        bounded_adjustment = 0
    bounded_adjustment = int(_bounded(bounded_adjustment, -MAX_MODEL_ADJUSTMENT, MAX_MODEL_ADJUSTMENT))
    normalized_reason = re.sub(r"\s+", " ", str(reason or "")).strip()[:240]
    if bounded_adjustment and not normalized_reason:
        bounded_adjustment = 0
    try:
        bounded_confidence = int(round(float(confidence)))
    except (TypeError, ValueError):
        bounded_confidence = 0
    event.model_adjustment = bounded_adjustment
    event.model_reason = normalized_reason
    event.model_confidence = int(_bounded(bounded_confidence, 0, 100))
    event.priority_score = int(
        _bounded(
            event.base_priority_score
            + event.profile_adjustment
            + event.feedback_adjustment
            + event.model_adjustment,
            0,
            100,
        )
    )
    event.priority_label = (
        "高优先级" if event.priority_score >= 72
        else "值得观察" if event.priority_score >= 50
        else "低优先级"
    )
    return event


def _feedback_age_factor(value: str, now: datetime) -> float:
    if not value:
        return 1.0
    try:
        created = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return 1.0
    age_days = max(0.0, (now - created).total_seconds() / 86400)
    return max(0.25, 1.0 - age_days / 90.0)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9+.#-]*|[\u4e00-\u9fff]", text.lower())
    return {word for word in words if len(word) > 1 or "\u4e00" <= word <= "\u9fff"}


def _similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _clean_summary(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(r"(?:Article|Comments) URL:\s*https?://\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bPoints:\s*\d+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"#\s*Comments:\s*\d+", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" .")


def normalize_x_copy(value: str, limit: int = 280) -> str:
    text = re.sub(r"[ \t]+", " ", value).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) <= limit:
        return text
    clipped = text[: limit - 1].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped.rstrip(" ,.;:-") + "…"


def normalize_model_text(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip(" ,.;:，。；：-") + "…"


def _truncate_words(value: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    clipped = value[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return (clipped or value[: limit - 1]).rstrip() + "…"


def _parse_date(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def assess_item_quality(item: SourceItem, now: datetime | None = None) -> tuple[int, list[str]]:
    """Score completeness and source risk without changing priority scoring."""
    current = now or datetime.now(timezone.utc)
    body = (item.content or item.summary).strip()
    score = QUALITY_SOURCE_SCORES.get(item.source_type, 60)
    flags: list[str] = []
    if len(item.title.strip()) < 12:
        score -= 12
        flags.append("标题信息不足")
    if len(body) < 180:
        score -= 18
        flags.append("正文内容较短")
    elif len(body) >= 500:
        score += 6
    if item.content_status in {"summary_fallback", "rss_summary", "skipped"}:
        score -= 12
        flags.append("正文未抽取，使用 RSS 摘要")
    elif item.content_status == "failed":
        score -= 15
        flags.append("正文抽取失败")
    if not item.published_at:
        score -= 8
        flags.append("发布时间缺失")
    else:
        age_hours = max(0.0, (current - _parse_date(item.published_at).astimezone(timezone.utc)).total_seconds() / 3600)
        if age_hours > 72:
            score -= 8
            flags.append("内容时效性偏低")
    lowered = f"{item.title} {body}".lower()
    if any(token in lowered for token in ("rumor", "alleged", "unverified", "未经证实")):
        score -= 15
        flags.append("可能存在未经证实信息")
    if item.title.isupper() and len(item.title) > 12:
        score -= 6
        flags.append("标题可能夸张")
    return int(_bounded(score, 0, 100)), list(dict.fromkeys(flags))


def assess_event_quality(items: Sequence[SourceItem], now: datetime | None = None) -> tuple[int, list[str]]:
    scores: list[int] = []
    flags: list[str] = []
    for item in items:
        score, item_flags = assess_item_quality(item, now=now)
        item.quality_score = score
        item.quality_flags = item_flags
        scores.append(score)
        flags.extend(item_flags)
    return (round(sum(scores) / len(scores)) if scores else 0), list(dict.fromkeys(flags))


def _profile_adjustment(profile: AccountProfile, items: Sequence[SourceItem]) -> tuple[int, list[str], bool]:
    combined = " ".join(f"{item.title} {item.summary}" for item in items).lower()
    focus_hits = [topic for topic in profile.focus_topics if _topic_matches(topic, combined)]
    avoid_hits = [topic for topic in profile.avoid_topics if _topic_matches(topic, combined)]
    adjustment = min(8, len(focus_hits) * 4) - min(12, len(avoid_hits) * 6)
    reasons: list[str] = []
    if focus_hits:
        reasons.append(f"命中关注主题：{', '.join(focus_hits)}（+{min(8, len(focus_hits) * 4)}）")
    if avoid_hits:
        reasons.append(f"命中回避主题：{', '.join(avoid_hits)}（-{min(12, len(avoid_hits) * 6)}）")
    return int(_bounded(adjustment, -MAX_PROFILE_ADJUSTMENT, MAX_PROFILE_ADJUSTMENT)), reasons, bool(focus_hits)


def _event_heat_score(items: Sequence[SourceItem]) -> int:
    return max((item.heat_score for item in items), default=0)


def _event_credibility_score(items: Sequence[SourceItem]) -> int:
    by_source: dict[str, int] = {}
    for item in items:
        by_source[item.source_name] = max(
            by_source.get(item.source_name, 0),
            SOURCE_CREDIBILITY_SCORES.get(item.source_type, 55),
        )
    return round(sum(by_source.values()) / len(by_source)) if by_source else 0


def assess_relevance(
    items: Sequence[SourceItem],
    profile: AccountProfile | None = None,
) -> tuple[int, str, str]:
    """Produce a model-free account relevance score and a binary recommendation."""
    active_profile = profile or AccountProfile()
    combined = " ".join(f"{item.title} {item.content or item.summary}" for item in items).lower()
    keyword_hits = [keyword for keyword in AI_KEYWORDS if _topic_matches(keyword, combined)]
    focus_hits = [topic for topic in active_profile.focus_topics if _topic_matches(topic, combined)]
    avoid_hits = [topic for topic in active_profile.avoid_topics if _topic_matches(topic, combined)]
    relevance_score = int(_bounded(
        10 + min(60, len(keyword_hits) * 12) + min(30, len(focus_hits) * 15)
        - min(50, len(avoid_hits) * 25),
        0,
        100,
    ))
    unverified = any(
        token in combined for token in ("rumor", "alleged", "unverified", "未经证实")
    )
    recommendation = (
        "推荐" if relevance_score >= 45 and not avoid_hits and not unverified else "不推荐"
    )
    signals: list[str] = []
    if keyword_hits:
        signals.append(f"AI 领域信号 {len(keyword_hits)} 个")
    if focus_hits:
        signals.append(f"命中关注主题：{', '.join(focus_hits)}")
    if avoid_hits:
        signals.append(f"命中回避主题：{', '.join(avoid_hits)}")
    if unverified:
        signals.append("存在未经证实风险")
    if not signals:
        signals.append("未命中账号关注信号")
    reason = f"规则相关性 {relevance_score}/100；" + "；".join(signals) + "。"
    return relevance_score, recommendation, reason


def _score_event(
    items: list[SourceItem],
    now: datetime | None = None,
    profile: AccountProfile | None = None,
    feedback: FeedbackProfile | None = None,
) -> tuple[int, str, str, str, list[str], int, int, int, int, int, int, str, str]:
    combined = " ".join(f"{item.title} {item.content or item.summary}" for item in items).lower()
    keyword_hits = sum(1 for keyword in AI_KEYWORDS if _topic_matches(keyword, combined))
    source_diversity = len({item.source_name for item in items})
    current = now or datetime.now(timezone.utc)
    newest = max((_parse_date(item.published_at) for item in items), default=current)
    age_hours = max(0.0, (current - newest.astimezone(timezone.utc)).total_seconds() / 3600)
    freshness = max(0, 30 - int(age_hours / 8))
    topic_signal = min(25, keyword_hits * 4)
    diversity = min(15, source_diversity * 5)
    heat_score = _event_heat_score(items)
    credibility_score = _event_credibility_score(items)
    heat_component = round(heat_score * 0.15)
    credibility_component = round(credibility_score * 0.15)
    base_score = min(
        100,
        freshness + topic_signal + diversity + heat_component + credibility_component,
    )

    active_profile = profile or AccountProfile()
    profile_adjustment, profile_reasons, _focus_hit = _profile_adjustment(active_profile, items)
    feedback_adjustment, feedback_reasons = (feedback or FeedbackProfile()).adjustment_for(items)
    score = int(_bounded(base_score + profile_adjustment + feedback_adjustment, 0, 100))
    label = "高优先级" if score >= 72 else "值得观察" if score >= 50 else "低优先级"
    relevance_score, recommendation, recommendation_reason = assess_relevance(items, active_profile)
    if recommendation == "推荐" and score >= 72 and source_diversity >= 2:
        decision = "建议跟进"
        reason = "事件较新，已获多来源验证，且规则相关性达到推荐门槛。"
    elif recommendation == "推荐":
        decision = "建议评估"
        reason = "规则相关性达到推荐门槛，但仍需人工确认来源证据和账号角度。"
    else:
        decision = "暂不跟进"
        reason = "规则相关性未达到推荐门槛或存在阻断风险，保留观察。"
    reason += f" {recommendation_reason}"
    reason += (
        f" 基础分构成：时效 {freshness}、主题 {topic_signal}、多源验证 {diversity}、"
        f"真实热度 {heat_component}、来源可信度 {credibility_component}。"
    )
    adjustment_reasons = profile_reasons + feedback_reasons
    if adjustment_reasons:
        reason += " 调整：" + "；".join(adjustment_reasons) + "。"
    risks: list[str] = []
    if source_diversity == 1:
        risks.append("单一来源，建议核验")
    if any(word in combined for word in ("rumor", "alleged", "未经证实")):
        risks.append("可能存在未经证实信息")
    if active_profile.avoid_topics and any(_topic_matches(topic, combined) for topic in active_profile.avoid_topics):
        risks.append("命中账号回避主题")
    return (
        score, label, decision, reason, risks, base_score,
        profile_adjustment, feedback_adjustment, heat_score, credibility_score,
        relevance_score, recommendation, recommendation_reason,
    )


def cluster_items(items: Iterable[SourceItem], threshold: float = 0.28) -> list[list[SourceItem]]:
    clusters: list[list[SourceItem]] = []
    for item in items:
        placed = False
        for cluster in clusters:
            representative = cluster[0]
            title_similarity = _similarity(item.title, representative.title)
            similarity = title_similarity
            if item.source_name != representative.source_name:
                body_similarity = _similarity(
                    item.title + " " + item.summary,
                    representative.title + " " + representative.summary,
                )
                similarity = max(title_similarity, body_similarity)
            if similarity >= threshold:
                cluster.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])
    return clusters


def _event_from_cluster(
    cluster: list[SourceItem],
    index: int,
    now: datetime | None = None,
    profile: AccountProfile | None = None,
    feedback: FeedbackProfile | None = None,
) -> Event:
    quality_score, quality_flags = assess_event_quality(cluster, now=now)
    representative = max(cluster, key=lambda item: len(item.title))
    unique_summaries: list[str] = []
    summary_items = sorted(
        cluster,
        key=lambda item: QUALITY_SOURCE_SCORES.get(item.source_type, 60),
        reverse=True,
    )
    for item in summary_items:
        summary = _clean_summary(item.summary)
        if summary and summary not in unique_summaries:
            unique_summaries.append(summary)
        if len(unique_summaries) == 3:
            break
    digest = " ".join(unique_summaries)[:700] or representative.title
    (
        score, label, decision, reason, risks, base_score,
        profile_adjustment, feedback_adjustment, heat_score, credibility_score,
        relevance_score, recommendation, recommendation_reason,
    ) = _score_event(
        cluster,
        now=now,
        profile=profile,
        feedback=feedback,
    )
    reason += f" 内容质量评估：{quality_score}/100。"
    risks = list(dict.fromkeys([*risks, *quality_flags]))
    event_key = "|".join(sorted(item.item_id for item in cluster)) or str(index)
    event_id = hashlib.sha1(event_key.encode("utf-8")).hexdigest()[:16]
    return Event(
        event_id=event_id,
        title=representative.title,
        summary=digest,
        items=cluster,
        priority_score=score,
        priority_label=label,
        follow_decision=decision,
        follow_reason=reason,
        risk_flags=risks,
        base_priority_score=base_score,
        profile_adjustment=profile_adjustment,
        feedback_adjustment=feedback_adjustment,
        quality_score=quality_score,
        quality_flags=quality_flags,
        heat_score=heat_score,
        credibility_score=credibility_score,
        relevance_score=relevance_score,
        recommendation=recommendation,
        recommendation_reason=recommendation_reason,
    )


def fallback_candidate(event: Event) -> Candidate:
    source_names = ", ".join(sorted({item.source_name for item in event.items}))
    source_type_count = len({item.source_type for item in event.items})
    angle = "从事件对 AI 产品工作流和用户预期的实际影响切入，避免复述标题或夸大结论。"
    if source_type_count == 1:
        angle += " 当前只有单一来源类型，文案需保留核验语气。"

    headline = _truncate_words(event.title, 96)
    summary = _clean_summary(event.summary) or "The signal is early and still needs source verification."
    takeaway = {
        "建议跟进": "Multiple source types make this worth a closer look for AI product teams.",
        "建议评估": "The signal is relevant to AI product teams, but its implications still need verification.",
        "暂不跟进": "The signal is early; more evidence is needed before making a stronger claim.",
    }.get(event.follow_decision, "The implications still need verification.")
    fixed_text = f"{headline}\n\nWhy it matters: \n\n{takeaway}"
    summary_budget = max(48, 280 - len(fixed_text))
    english_copy = normalize_x_copy(
        f"{headline}\n\nWhy it matters: {_truncate_words(summary, summary_budget)}\n\n{takeaway}"
    )
    if event.follow_decision == "暂不跟进":
        angle = "先保留为观察项，不直接追热点，等待更多来源或更明确的用户价值。"
    return Candidate(
        candidate_id=event.event_id,
        event=event,
        content_angle=f"{angle} 来源：{source_names}。",
        english_copy=english_copy,
        optimized_title=event.title,
        optimized_summary=event.summary,
    )


def analyze_items(
    items: list[SourceItem],
    now: datetime | None = None,
    profile: AccountProfile | None = None,
    feedback: FeedbackProfile | None = None,
) -> list[Candidate]:
    clusters = cluster_items(items)
    events = [
        _event_from_cluster(cluster, index, now=now, profile=profile, feedback=feedback)
        for index, cluster in enumerate(clusters)
    ]
    events.sort(key=lambda event: event.priority_score, reverse=True)
    return [fallback_candidate(event) for event in events]
