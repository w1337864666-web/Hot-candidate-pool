from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

from .chat_store import ChatStore
from .core import AccountProfile
from .pipeline import run_scan
from .sources import DOMESTIC_SOURCES


def _profile_from_plan(plan: Mapping[str, object]) -> AccountProfile:
    profile = plan.get("profile")
    if not isinstance(profile, Mapping):
        profile = {}
    return AccountProfile.from_form(
        audience=str(profile.get("audience") or "英语市场的 AI 用户、创作者和产品团队"),
        focus_topics=profile.get("focus_topics") or "AI products, agents, developer tools, creator workflows",
        avoid_topics=profile.get("avoid_topics") or "",
        tone=str(profile.get("tone") or "简洁、专业、有明确观点，不过度宣传"),
        free_text=str(profile.get("free_text") or ""),
    )


def run_chat_task(task_id: str) -> None:
    """Run one claimed chat task in its own SQLite connection and thread."""
    with ChatStore() as chat_store:
        task = chat_store.get_task(task_id)
        if not task:
            return
        started = datetime.now(timezone.utc).isoformat()
        chat_store.update_task(
            task_id,
            status="running",
            progress=12,
            progress_label="正在读取来源并整理热点",
            started_at=started,
        )
        try:
            plan = task["plan"]
            mode = str(plan.get("source_mode") or "demo")
            if mode not in {"demo", "live", "domestic"}:
                mode = "demo"
            profile = _profile_from_plan(plan)
            configs = DOMESTIC_SOURCES if mode == "domestic" else None
            run_id, errors, candidates = run_scan(
                mode,
                profile,
                configs=configs,
                trigger_type="manual",
                persist_candidates=str(plan.get("output_mode") or "candidate_pool") == "candidate_pool",
            )
            status = "partial_success" if errors else "succeeded"
            output_mode = str(plan.get("output_mode") or "candidate_pool")
            result = {
                "run_id": run_id,
                "mode": mode,
                "output_mode": output_mode,
                "candidate_count": len(candidates) if output_mode == "candidate_pool" else 0,
                "error_count": len(errors),
                "errors": [str(error) for error in errors[:5]],
            }
            label = "完成，部分来源有错误" if errors else ("完成，候选已写入内容池" if output_mode == "candidate_pool" else "完成，结果已保存到运行记录")
            chat_store.update_task(
                task_id,
                status=status,
                progress=100,
                progress_label=label,
                run_id=run_id,
                result=result,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:  # keep task errors visible in the demo UI
            chat_store.update_task(
                task_id,
                status="failed",
                progress=100,
                progress_label="任务失败",
                error=f"{type(exc).__name__}: {exc}",
                result={},
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
