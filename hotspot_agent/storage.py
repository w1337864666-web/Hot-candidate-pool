from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import get_database_path
from .core import (
    SOURCE_CREDIBILITY_SCORES,
    AccountProfile,
    Candidate,
    FeedbackSignal,
    SourceItem,
    assess_event_quality,
    assess_relevance,
)
from .sources import SourceConfig


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS articles (
    article_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    content_status TEXT NOT NULL DEFAULT 'rss_summary',
    content_source TEXT NOT NULL DEFAULT 'rss_summary',
    content_error TEXT NOT NULL DEFAULT '',
    content_fetched_at TEXT NOT NULL DEFAULT '',
    quality_score INTEGER NOT NULL DEFAULT 0,
    quality_flags_json TEXT NOT NULL DEFAULT '[]',
    engagement_points INTEGER,
    engagement_comments INTEGER,
    heat_score INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(source_id) REFERENCES sources(source_id)
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    priority_score INTEGER NOT NULL,
    base_score INTEGER NOT NULL DEFAULT 0,
    profile_adjustment INTEGER NOT NULL DEFAULT 0,
    feedback_adjustment INTEGER NOT NULL DEFAULT 0,
    model_adjustment INTEGER NOT NULL DEFAULT 0,
    model_reason TEXT NOT NULL DEFAULT '',
    model_confidence INTEGER NOT NULL DEFAULT 0,
    priority_label TEXT NOT NULL,
    follow_decision TEXT NOT NULL,
    follow_reason TEXT NOT NULL,
    risk_flags_json TEXT NOT NULL,
    quality_score INTEGER NOT NULL DEFAULT 0,
    quality_flags_json TEXT NOT NULL DEFAULT '[]',
    heat_score INTEGER NOT NULL DEFAULT 0,
    credibility_score INTEGER NOT NULL DEFAULT 0,
    relevance_score INTEGER NOT NULL DEFAULT 0,
    recommendation TEXT NOT NULL DEFAULT '不推荐',
    recommendation_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_articles (
    event_id TEXT NOT NULL,
    article_id TEXT NOT NULL,
    PRIMARY KEY(event_id, article_id),
    FOREIGN KEY(event_id) REFERENCES events(event_id),
    FOREIGN KEY(article_id) REFERENCES articles(article_id)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    source_count INTEGER NOT NULL,
    item_count INTEGER NOT NULL,
    candidate_count INTEGER NOT NULL,
    errors_json TEXT NOT NULL DEFAULT '[]',
    profile_json TEXT NOT NULL DEFAULT '{}',
    source_stats_json TEXT NOT NULL DEFAULT '[]',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    trigger_type TEXT NOT NULL DEFAULT 'manual'
);
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    content_angle TEXT NOT NULL,
    english_copy TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    ai_status TEXT NOT NULL DEFAULT 'template',
    optimized_title TEXT NOT NULL DEFAULT '',
    optimized_summary TEXT NOT NULL DEFAULT '',
    ai_review_status TEXT NOT NULL DEFAULT 'fallback',
    ai_review_reason TEXT NOT NULL DEFAULT '',
    ai_review_confidence INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    deleted_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(event_id) REFERENCES events(event_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS review_actions (
    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    action TEXT NOT NULL,
    english_copy TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
);
CREATE TABLE IF NOT EXISTS app_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scheduled_scans (
    schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_date TEXT NOT NULL UNIQUE,
    scheduled_for TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT '',
    run_id INTEGER,
    error TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
"""

VALID_STATUSES = {"approved", "edited", "rejected"}
SOURCE_ORIGIN_LABELS = {
    "demo": "演示快照",
    "domestic": "国内来源",
    "overseas": "海外来源",
}
AI_REVIEW_LABELS = {
    "passed": "AI 审核通过",
    "rejected": "AI 审核未通过",
    "fallback": "规则初筛兜底",
    "not_evaluated": "本轮未评估",
}
AI_GATE_STATUSES = ("passed", "fallback")


def _source_origin(mode: str, fallback_used: bool) -> str:
    if mode == "demo" or fallback_used:
        return "demo"
    if mode == "domestic":
        return "domestic"
    return "overseas"


def _source_id(config: SourceConfig | SourceItem) -> str:
    return f"source:{config.source_name}:{config.url}"


class Store:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else get_database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self._migrate_schema()
        self.connection.commit()

    def _migrate_schema(self) -> None:
        migrations = {
            "events": {
                "base_score": "INTEGER NOT NULL DEFAULT 0",
                "profile_adjustment": "INTEGER NOT NULL DEFAULT 0",
                "feedback_adjustment": "INTEGER NOT NULL DEFAULT 0",
                "model_adjustment": "INTEGER NOT NULL DEFAULT 0",
                "model_reason": "TEXT NOT NULL DEFAULT ''",
                "model_confidence": "INTEGER NOT NULL DEFAULT 0",
                "quality_score": "INTEGER NOT NULL DEFAULT 0",
                "quality_flags_json": "TEXT NOT NULL DEFAULT '[]'",
                "heat_score": "INTEGER NOT NULL DEFAULT 0",
                "credibility_score": "INTEGER NOT NULL DEFAULT 0",
                "relevance_score": "INTEGER NOT NULL DEFAULT 0",
                "recommendation": "TEXT NOT NULL DEFAULT '不推荐'",
                "recommendation_reason": "TEXT NOT NULL DEFAULT ''",
            },
            "articles": {
                "content": "TEXT NOT NULL DEFAULT ''",
                "content_status": "TEXT NOT NULL DEFAULT 'rss_summary'",
                "content_source": "TEXT NOT NULL DEFAULT 'rss_summary'",
                "content_error": "TEXT NOT NULL DEFAULT ''",
                "content_fetched_at": "TEXT NOT NULL DEFAULT ''",
                "quality_score": "INTEGER NOT NULL DEFAULT 0",
                "quality_flags_json": "TEXT NOT NULL DEFAULT '[]'",
                "engagement_points": "INTEGER",
                "engagement_comments": "INTEGER",
                "heat_score": "INTEGER NOT NULL DEFAULT 0",
            },
            "runs": {
                "profile_json": "TEXT NOT NULL DEFAULT '{}'",
                "source_stats_json": "TEXT NOT NULL DEFAULT '[]'",
                "duration_ms": "INTEGER NOT NULL DEFAULT 0",
                "fallback_used": "INTEGER NOT NULL DEFAULT 0",
                "trigger_type": "TEXT NOT NULL DEFAULT 'manual'",
            },
            "candidates": {
                "deleted_at": "TEXT NOT NULL DEFAULT ''",
                "optimized_title": "TEXT NOT NULL DEFAULT ''",
                "optimized_summary": "TEXT NOT NULL DEFAULT ''",
                "ai_review_status": "TEXT NOT NULL DEFAULT 'fallback'",
                "ai_review_reason": "TEXT NOT NULL DEFAULT ''",
                "ai_review_confidence": "INTEGER NOT NULL DEFAULT 0",
            },
            "scheduled_scans": {
                "schedule_date": "TEXT NOT NULL DEFAULT ''",
            },
        }
        for table, columns in migrations.items():
            existing = {row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in existing:
                    self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        self.connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS scheduled_scans_schedule_date_unique
               ON scheduled_scans(schedule_date) WHERE schedule_date <> ''"""
        )

        # Legacy events only had priority_score; preserve that value as the
        # explainable base score when the new split has not been populated.
        self.connection.execute(
            """UPDATE events
               SET base_score = priority_score
               WHERE base_score = 0 AND priority_score > 0
                 AND profile_adjustment = 0 AND feedback_adjustment = 0"""
        )
        self._backfill_quality()
        self._backfill_event_signals()

    def _backfill_quality(self) -> None:
        """Populate explainable quality fields for rows created before quality scoring."""
        rows = self.connection.execute(
            "SELECT event_id, follow_reason, risk_flags_json FROM events WHERE quality_score = 0"
        ).fetchall()
        for row in rows:
            article_rows = self.connection.execute(
                """SELECT s.source_type, s.source_name, a.title, a.summary, a.url, a.published_at,
                          a.content, a.content_status, a.content_source, a.content_error, a.content_fetched_at
                   FROM event_articles ea JOIN articles a ON a.article_id = ea.article_id
                   JOIN sources s ON s.source_id = a.source_id WHERE ea.event_id = ?""",
                (row["event_id"],),
            ).fetchall()
            items = [
                SourceItem(
                    source_type=article["source_type"], source_name=article["source_name"],
                    title=article["title"], summary=article["summary"], url=article["url"],
                    published_at=article["published_at"], content=article["content"] or article["summary"],
                    content_status=article["content_status"], content_source=article["content_source"],
                    content_error=article["content_error"], content_fetched_at=article["content_fetched_at"],
                )
                for article in article_rows
            ]
            if not items:
                continue
            score, flags = assess_event_quality(items)
            existing_risks = json.loads(row["risk_flags_json"] or "[]")
            risks = list(dict.fromkeys([*existing_risks, *flags]))
            reason = row["follow_reason"] or ""
            if "内容质量评估" not in reason:
                reason = f"{reason} 内容质量评估：{score}/100。".strip()
            self.connection.execute(
                "UPDATE events SET quality_score = ?, quality_flags_json = ?, risk_flags_json = ?, follow_reason = ? WHERE event_id = ?",
                (score, json.dumps(flags, ensure_ascii=False), json.dumps(risks, ensure_ascii=False), reason, row["event_id"]),
            )
            for item in items:
                self.connection.execute(
                    "UPDATE articles SET quality_score = ?, quality_flags_json = ? WHERE url = ?",
                    (item.quality_score, json.dumps(item.quality_flags, ensure_ascii=False), item.url),
                )

    def _backfill_event_signals(self) -> None:
        """Populate new explainable fields while preserving historical priority scores."""
        event_rows = self.connection.execute(
            "SELECT event_id FROM events WHERE recommendation_reason = '' OR credibility_score = 0"
        ).fetchall()
        for event_row in event_rows:
            article_rows = self.connection.execute(
                """SELECT s.source_type, s.source_name, a.title, a.summary, a.url, a.published_at,
                          a.content, a.content_status, a.content_source, a.content_error,
                          a.content_fetched_at, a.engagement_points, a.engagement_comments,
                          a.heat_score
                   FROM event_articles ea JOIN articles a ON a.article_id = ea.article_id
                   JOIN sources s ON s.source_id = a.source_id WHERE ea.event_id = ?""",
                (event_row["event_id"],),
            ).fetchall()
            items = [
                SourceItem(
                    source_type=row["source_type"], source_name=row["source_name"],
                    title=row["title"], summary=row["summary"], url=row["url"],
                    published_at=row["published_at"], content=row["content"] or row["summary"],
                    content_status=row["content_status"], content_source=row["content_source"],
                    content_error=row["content_error"], content_fetched_at=row["content_fetched_at"],
                    engagement_points=row["engagement_points"],
                    engagement_comments=row["engagement_comments"], heat_score=row["heat_score"],
                )
                for row in article_rows
            ]
            if not items:
                continue
            credibility_by_source = {
                item.source_name: SOURCE_CREDIBILITY_SCORES.get(item.source_type, 55)
                for item in items
            }
            credibility_score = round(
                sum(credibility_by_source.values()) / len(credibility_by_source)
            )
            relevance_score, recommendation, recommendation_reason = assess_relevance(items)
            self.connection.execute(
                """UPDATE events SET heat_score = ?, credibility_score = ?, relevance_score = ?,
                     recommendation = ?, recommendation_reason = ? WHERE event_id = ?""",
                (
                    max((item.heat_score for item in items), default=0),
                    credibility_score,
                    relevance_score,
                    recommendation,
                    recommendation_reason,
                    event_row["event_id"],
                ),
            )

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def app_settings(self) -> dict[str, str]:
        rows = self.connection.execute(
            "SELECT setting_key, setting_value FROM app_settings"
        ).fetchall()
        return {str(row["setting_key"]): str(row["setting_value"]) for row in rows}

    def save_app_settings(self, values: dict[str, object]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for key, value in values.items():
            self.connection.execute(
                """INSERT INTO app_settings(setting_key, setting_value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(setting_key) DO UPDATE SET
                     setting_value=excluded.setting_value, updated_at=excluded.updated_at""",
                (str(key), str(value), now),
            )
        self.connection.commit()

    def save_run(
        self,
        mode: str,
        configs: Iterable[SourceConfig],
        items: list[SourceItem],
        candidates: list[Candidate],
        errors: list[str],
        profile: AccountProfile | None = None,
        source_stats: list[dict[str, object]] | None = None,
        duration_ms: int = 0,
        fallback_used: bool = False,
        trigger_type: str = "manual",
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        configs_by_name = {config.source_name: config for config in configs}
        for item in items:
            configs_by_name.setdefault(item.source_name, SourceConfig(item.source_type, item.source_name, item.url))
        configs = list(configs_by_name.values())
        source_by_name = configs_by_name
        source_names = {item.source_name for item in items}
        cursor = self.connection.execute(
            "INSERT INTO runs(mode, started_at, source_count, item_count, candidate_count, errors_json, profile_json, source_stats_json, duration_ms, fallback_used, trigger_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                mode,
                now,
                len(configs),
                len(items),
                len(candidates),
                json.dumps(errors, ensure_ascii=False),
                json.dumps((profile or AccountProfile()).to_dict(), ensure_ascii=False),
                json.dumps(source_stats or [], ensure_ascii=False),
                duration_ms,
                int(fallback_used),
                trigger_type if trigger_type in {"manual", "scheduled"} else "manual",
            ),
        )
        run_id = int(cursor.lastrowid)
        for config in configs:
            self.connection.execute(
                "INSERT INTO sources(source_id, source_type, source_name, url) VALUES (?, ?, ?, ?) ON CONFLICT(url) DO UPDATE SET source_type=excluded.source_type, source_name=excluded.source_name",
                (_source_id(config), config.source_type, config.source_name, config.url),
            )
        for item in items:
            config = source_by_name.get(item.source_name) or SourceConfig(item.source_type, item.source_name, item.url)
            self.connection.execute(
                """INSERT INTO articles(
                    article_id, source_id, title, summary, url, published_at, fetched_at,
                    content, content_status, content_source, content_error, content_fetched_at,
                    quality_score, quality_flags_json, engagement_points, engagement_comments,
                    heat_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                  source_id=excluded.source_id, title=excluded.title, summary=excluded.summary,
                  url=excluded.url, published_at=excluded.published_at, fetched_at=excluded.fetched_at,
                  content=excluded.content, content_status=excluded.content_status,
                  content_source=excluded.content_source, content_error=excluded.content_error,
                  content_fetched_at=excluded.content_fetched_at, quality_score=excluded.quality_score,
                  quality_flags_json=excluded.quality_flags_json,
                  engagement_points=excluded.engagement_points,
                  engagement_comments=excluded.engagement_comments, heat_score=excluded.heat_score""",
                (
                    item.item_id, _source_id(config), item.title, item.summary, item.url,
                    item.published_at, now, item.content, item.content_status, item.content_source,
                    item.content_error, item.content_fetched_at, item.quality_score,
                    json.dumps(item.quality_flags, ensure_ascii=False),
                    item.engagement_points, item.engagement_comments, item.heat_score,
                ),
            )
        for candidate in candidates:
            event = candidate.event
            self.connection.execute(
                """INSERT INTO events(event_id, title, summary, priority_score, base_score, profile_adjustment,
                   feedback_adjustment, model_adjustment, model_reason, model_confidence,
                   priority_label, follow_decision, follow_reason, risk_flags_json,
                   quality_score, quality_flags_json, heat_score, credibility_score,
                   relevance_score, recommendation, recommendation_reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(event_id) DO UPDATE SET
                     title=excluded.title, summary=excluded.summary, priority_score=excluded.priority_score,
                     base_score=excluded.base_score, profile_adjustment=excluded.profile_adjustment,
                     feedback_adjustment=excluded.feedback_adjustment,
                     model_adjustment=excluded.model_adjustment, model_reason=excluded.model_reason,
                     model_confidence=excluded.model_confidence, priority_label=excluded.priority_label,
                     follow_decision=excluded.follow_decision, follow_reason=excluded.follow_reason,
                     risk_flags_json=excluded.risk_flags_json, quality_score=excluded.quality_score,
                     quality_flags_json=excluded.quality_flags_json, heat_score=excluded.heat_score,
                     credibility_score=excluded.credibility_score,
                     relevance_score=excluded.relevance_score,
                     recommendation=excluded.recommendation,
                     recommendation_reason=excluded.recommendation_reason,
                     created_at=excluded.created_at""",
                (
                    event.event_id,
                    event.title,
                    event.summary,
                    event.priority_score,
                    event.base_priority_score,
                    event.profile_adjustment,
                    event.feedback_adjustment,
                    event.model_adjustment,
                    event.model_reason,
                    event.model_confidence,
                    event.priority_label,
                    event.follow_decision,
                    event.follow_reason,
                    json.dumps(event.risk_flags, ensure_ascii=False),
                    event.quality_score,
                    json.dumps(event.quality_flags, ensure_ascii=False),
                    event.heat_score,
                    event.credibility_score,
                    event.relevance_score,
                    event.recommendation,
                    event.recommendation_reason,
                    now,
                ),
            )
            for item in event.items:
                self.connection.execute(
                    "INSERT OR IGNORE INTO event_articles(event_id, article_id) VALUES (?, ?)",
                    (event.event_id, item.item_id),
                )
            self.connection.execute(
                """INSERT INTO candidates(
                     candidate_id, event_id, run_id, content_angle, english_copy, status, ai_status,
                     optimized_title, optimized_summary, ai_review_status, ai_review_reason,
                     ai_review_confidence, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(candidate_id) DO UPDATE SET
                     event_id=excluded.event_id, run_id=excluded.run_id,
                     content_angle=excluded.content_angle,
                     english_copy=CASE WHEN candidates.status = 'pending' THEN excluded.english_copy ELSE candidates.english_copy END,
                     ai_status=CASE WHEN candidates.status = 'pending' THEN excluded.ai_status ELSE candidates.ai_status END,
                     optimized_title=CASE WHEN candidates.status = 'pending' THEN excluded.optimized_title ELSE candidates.optimized_title END,
                     optimized_summary=CASE WHEN candidates.status = 'pending' THEN excluded.optimized_summary ELSE candidates.optimized_summary END,
                     ai_review_status=CASE WHEN candidates.status = 'pending' THEN excluded.ai_review_status ELSE candidates.ai_review_status END,
                     ai_review_reason=CASE WHEN candidates.status = 'pending' THEN excluded.ai_review_reason ELSE candidates.ai_review_reason END,
                     ai_review_confidence=CASE WHEN candidates.status = 'pending' THEN excluded.ai_review_confidence ELSE candidates.ai_review_confidence END,
                     updated_at=excluded.updated_at""",
                (
                    candidate.candidate_id, event.event_id, run_id, candidate.content_angle,
                    candidate.english_copy, candidate.status, candidate.ai_status,
                    candidate.optimized_title, candidate.optimized_summary,
                    candidate.ai_review_status, candidate.ai_review_reason,
                    candidate.ai_review_confidence, now,
                ),
            )
        self.connection.commit()
        return run_id

    def _candidate_query(self, where: str = "", params: tuple = ()) -> list[dict]:
        rows = self.connection.execute(
            f"""SELECT c.*, r.mode AS run_mode, r.fallback_used AS run_fallback_used,
                e.title, e.summary, e.priority_score, e.base_score,
                e.profile_adjustment, e.feedback_adjustment, e.model_adjustment,
                e.model_reason, e.model_confidence, e.priority_label,
                e.follow_decision, e.follow_reason, e.risk_flags_json,
                e.quality_score, e.quality_flags_json, e.heat_score,
                e.credibility_score, e.relevance_score, e.recommendation,
                e.recommendation_reason
                FROM candidates c JOIN events e ON e.event_id = c.event_id
                JOIN runs r ON r.run_id = c.run_id
                {where} ORDER BY e.priority_score DESC, c.updated_at DESC""",
            params,
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            origin = _source_origin(str(item.pop("run_mode")), bool(item.pop("run_fallback_used")))
            item["source_origin"] = origin
            item["source_origin_label"] = SOURCE_ORIGIN_LABELS[origin]
            item["risk_flags"] = json.loads(item.pop("risk_flags_json"))
            item["quality_flags"] = json.loads(item.pop("quality_flags_json"))
            item["original_title"] = item["title"]
            item["original_summary"] = item["summary"]
            item["title"] = item["optimized_title"] or item["title"]
            item["summary"] = item["optimized_summary"] or item["summary"]
            item["ai_review_label"] = AI_REVIEW_LABELS.get(
                item["ai_review_status"], item["ai_review_status"]
            )
            if item["deleted_at"]:
                item["workflow_stage"] = "deleted"
                item["workflow_stage_label"] = "已删除"
            elif item["status"] in {"approved", "edited"}:
                item["workflow_stage"] = "final"
                item["workflow_stage_label"] = "候选二 · 最终池"
            elif item["status"] == "rejected":
                item["workflow_stage"] = "rejected"
                item["workflow_stage_label"] = "人工已驳回"
            elif item["ai_review_status"] in AI_GATE_STATUSES:
                item["workflow_stage"] = "first"
                item["workflow_stage_label"] = "候选一 · 待人工审核"
            else:
                item["workflow_stage"] = "ai_filtered"
                item["workflow_stage_label"] = "未进入候选一"
            item["sources"] = [dict(source) for source in self.connection.execute(
                """SELECT a.source_id, s.source_type, s.source_name, a.title, a.summary, a.content,
                          a.content_status, a.content_source, a.content_error, a.quality_score,
                          a.quality_flags_json, a.url, a.published_at,
                          a.engagement_points, a.engagement_comments, a.heat_score
                   FROM event_articles ea JOIN articles a ON a.article_id = ea.article_id
                   JOIN sources s ON s.source_id = a.source_id WHERE ea.event_id = ? ORDER BY a.published_at DESC""",
                (row["event_id"],),
            ).fetchall()]
            for source in item["sources"]:
                source["quality_flags"] = json.loads(source.pop("quality_flags_json"))
            item["review_history"] = [dict(review) for review in self.connection.execute(
                """SELECT action, english_copy, note, created_at
                   FROM review_actions WHERE candidate_id = ?
                   ORDER BY action_id DESC""",
                (row["candidate_id"],),
            ).fetchall()]
            result.append(item)
        return result

    def article_content_cache(self, urls: Iterable[str]) -> dict[str, dict[str, object]]:
        values = tuple(dict.fromkeys(url for url in urls if url))
        if not values:
            return {}
        placeholders = ",".join("?" for _ in values)
        rows = self.connection.execute(
            f"SELECT url, content, content_status, content_source, content_error, content_fetched_at FROM articles WHERE url IN ({placeholders}) AND content_status = 'extracted'",
            values,
        ).fetchall()
        return {row["url"]: dict(row) for row in rows if row["content_fetched_at"]}

    def feedback_signals(self) -> list[FeedbackSignal]:
        rows = self.connection.execute(
            """SELECT ra.action_id, ra.action, ra.created_at, e.title, e.summary, s.source_type
               FROM review_actions ra
               JOIN candidates c ON c.candidate_id = ra.candidate_id
               JOIN events e ON e.event_id = c.event_id
               LEFT JOIN event_articles ea ON ea.event_id = e.event_id
               LEFT JOIN articles a ON a.article_id = ea.article_id
               LEFT JOIN sources s ON s.source_id = a.source_id
               ORDER BY ra.created_at DESC"""
        ).fetchall()
        grouped: dict[int, dict] = {}
        for row in rows:
            key = row["action_id"]
            signal = grouped.setdefault(
                key,
                {
                    "action": row["action"],
                    "created_at": row["created_at"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "source_types": set(),
                },
            )
            if row["source_type"]:
                signal["source_types"].add(row["source_type"])
        return [
            FeedbackSignal(
                action=value["action"],
                title=value["title"],
                summary=value["summary"],
                source_types=tuple(sorted(value["source_types"])),
                created_at=value["created_at"],
            )
            for value in grouped.values()
        ]

    def list_candidates(self, status: str | None = None) -> list[dict]:
        if status == "deleted":
            return self._candidate_query("WHERE c.deleted_at <> ''")
        if status == "final":
            return self._candidate_query(
                "WHERE c.status IN ('approved', 'edited') AND c.deleted_at = ''"
            )
        if status == "ai_filtered":
            return self._candidate_query(
                "WHERE c.status = 'pending' AND c.ai_review_status NOT IN ('passed', 'fallback') AND c.deleted_at = ''"
            )
        if status == "pending":
            return self._candidate_query(
                "WHERE c.status = 'pending' AND c.ai_review_status IN ('passed', 'fallback') AND c.deleted_at = ''"
            )
        if status and status not in {"全部", "all"}:
            return self._candidate_query("WHERE c.status = ? AND c.deleted_at = ''", (status,))
        return self._candidate_query("WHERE c.deleted_at = ''")

    def status_counts(self) -> dict[str, int]:
        counts = {
            "all": 0, "pending": 0, "final": 0, "ai_filtered": 0,
            "approved": 0, "edited": 0, "rejected": 0, "deleted": 0,
        }
        for row in self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM candidates WHERE deleted_at = '' GROUP BY status"
        ):
            if row["status"] in counts:
                counts[row["status"]] = int(row["count"])
        counts["all"] = int(
            self.connection.execute("SELECT COUNT(*) FROM candidates WHERE deleted_at = ''").fetchone()[0]
        )
        counts["pending"] = int(self.connection.execute(
            "SELECT COUNT(*) FROM candidates WHERE status = 'pending' AND ai_review_status IN ('passed', 'fallback') AND deleted_at = ''"
        ).fetchone()[0])
        counts["ai_filtered"] = int(self.connection.execute(
            "SELECT COUNT(*) FROM candidates WHERE status = 'pending' AND ai_review_status NOT IN ('passed', 'fallback') AND deleted_at = ''"
        ).fetchone()[0])
        counts["final"] = counts["approved"] + counts["edited"]
        counts["deleted"] = int(
            self.connection.execute("SELECT COUNT(*) FROM candidates WHERE deleted_at <> ''").fetchone()[0]
        )
        return counts

    def get_candidate(self, candidate_id: str) -> dict | None:
        rows = self._candidate_query("WHERE c.candidate_id = ?", (candidate_id,))
        return rows[0] if rows else None

    def next_pending_candidate_id(self) -> str | None:
        row = self.connection.execute(
            """SELECT c.candidate_id
               FROM candidates c JOIN events e ON e.event_id = c.event_id
               WHERE c.status = 'pending' AND c.ai_review_status IN ('passed', 'fallback')
                 AND c.deleted_at = ''
               ORDER BY e.priority_score DESC, c.updated_at DESC LIMIT 1"""
        ).fetchone()
        return str(row["candidate_id"]) if row else None

    def review(self, candidate_id: str, status: str, english_copy: str, note: str) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"unsupported review status: {status}")
        now = datetime.now(timezone.utc).isoformat()
        current = self.connection.execute(
            "SELECT status, ai_review_status, deleted_at FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if current and current["status"] == "pending" and current["ai_review_status"] not in AI_GATE_STATUSES:
            raise ValueError("该热点尚未通过 AI 智能审核，不能进入人工终审。")
        cursor = self.connection.execute(
            "UPDATE candidates SET status = ?, english_copy = ?, updated_at = ? WHERE candidate_id = ? AND deleted_at = ''",
            (status, english_copy.strip(), now, candidate_id),
        )
        if cursor.rowcount == 0:
            existing = self.connection.execute(
                "SELECT deleted_at FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if existing and existing["deleted_at"]:
                raise ValueError("已删除候选不能审核，请先恢复。")
            raise KeyError(candidate_id)
        self.connection.execute(
            "INSERT INTO review_actions(candidate_id, action, english_copy, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (candidate_id, status, english_copy.strip(), note.strip(), now),
        )
        self.connection.commit()

    def delete_approved(self, candidate_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            """UPDATE candidates SET deleted_at = ?, updated_at = ?
               WHERE candidate_id = ? AND status IN ('approved', 'edited') AND deleted_at = ''""",
            (now, now, candidate_id),
        )
        if cursor.rowcount == 0:
            existing = self.connection.execute(
                "SELECT status, deleted_at FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if not existing:
                raise KeyError(candidate_id)
            if existing["deleted_at"]:
                raise ValueError("该候选已经删除。")
            raise ValueError("只有最终候选池中的热点可以删除。")
        self.connection.commit()

    def restore(self, candidate_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            """UPDATE candidates SET deleted_at = '', updated_at = ?
               WHERE candidate_id = ? AND deleted_at <> ''""",
            (now, candidate_id),
        )
        if cursor.rowcount == 0:
            existing = self.connection.execute(
                "SELECT candidate_id FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if not existing:
                raise KeyError(candidate_id)
            raise ValueError("该候选未被删除。")
        self.connection.commit()

    def claim_scheduled_scan(self, scheduled_for: str, mode: str) -> int | None:
        """Atomically claim one local-time schedule slot across workers."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO scheduled_scans(
                 schedule_date, scheduled_for, mode, status, started_at
               ) VALUES (?, ?, ?, 'running', ?)""",
            (scheduled_for[:10], scheduled_for, mode, now),
        )
        self.connection.commit()
        return int(cursor.lastrowid) if cursor.rowcount == 1 else None

    def complete_scheduled_scan(self, schedule_id: int, run_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """UPDATE scheduled_scans SET status = 'succeeded', completed_at = ?,
                 run_id = ?, error = '' WHERE schedule_id = ?""",
            (now, run_id, schedule_id),
        )
        self.connection.commit()

    def fail_scheduled_scan(self, schedule_id: int, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """UPDATE scheduled_scans SET status = 'failed', completed_at = ?, error = ?
               WHERE schedule_id = ?""",
            (now, str(error)[:1000], schedule_id),
        )
        self.connection.commit()

    def list_scheduled_scans(self, limit: int = 20) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """SELECT ss.*, r.fallback_used, r.item_count, r.candidate_count
               FROM scheduled_scans ss LEFT JOIN runs r ON r.run_id = ss.run_id
               ORDER BY ss.scheduled_for DESC LIMIT ?""",
            (max(1, min(100, limit)),),
        ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            item["fallback_used"] = bool(item["fallback_used"]) if item["fallback_used"] is not None else False
            result.append(item)
        return result

    def list_runs(self) -> list[dict]:
        rows = self.connection.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 30").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["errors"] = json.loads(item.pop("errors_json"))
            item["profile"] = json.loads(item.pop("profile_json"))
            item["source_stats"] = json.loads(item.pop("source_stats_json"))
            item["fallback_used"] = bool(item["fallback_used"])
            result.append(item)
        return result

    def close(self) -> None:
        self.connection.close()
