from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Callable

from .config import DEFAULT_PROFILE, get_schedule_settings
from .core import AccountProfile
from .pipeline import run_scan
from .sources import DOMESTIC_SOURCES
from .storage import Store


SCHEDULE_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")
SCHEDULER_POLL_SECONDS = 30
logger = logging.getLogger(__name__)


def run_due_schedule(
    now: datetime | None = None,
    store: Store | None = None,
    scan_fn: Callable[..., tuple[int, list[str], list[object]]] = run_scan,
) -> dict[str, object] | None:
    """Claim and run today's configured slot if its local execution time has passed."""
    current = (now or datetime.now(SCHEDULE_TIMEZONE)).astimezone(SCHEDULE_TIMEZONE)
    owned_store = store is None
    active_store = store or Store()
    try:
        settings = get_schedule_settings(active_store.app_settings())
        if not settings["schedule_enabled"]:
            return None
        hour, minute = (int(part) for part in str(settings["schedule_time"]).split(":"))
        scheduled_at = datetime.combine(
            current.date(),
            time(hour=hour, minute=minute),
            tzinfo=SCHEDULE_TIMEZONE,
        )
        if current < scheduled_at:
            return None

        mode = str(settings["schedule_mode"])
        schedule_id = active_store.claim_scheduled_scan(scheduled_at.isoformat(), mode)
        if schedule_id is None:
            return None

        profile = AccountProfile.from_form(free_text=DEFAULT_PROFILE)
        configs = DOMESTIC_SOURCES if mode == "domestic" else None
        try:
            run_id, errors, candidates = scan_fn(
                mode,
                profile,
                store=active_store,
                configs=configs,
                trigger_type="scheduled",
            )
        except Exception as exc:
            active_store.fail_scheduled_scan(schedule_id, str(exc))
            return {"schedule_id": schedule_id, "status": "failed", "error": str(exc)}
        active_store.complete_scheduled_scan(schedule_id, run_id)
        return {
            "schedule_id": schedule_id,
            "status": "succeeded",
            "run_id": run_id,
            "error_count": len(errors),
            "candidate_count": len(candidates),
        }
    finally:
        if owned_store:
            active_store.close()


async def scheduler_loop(poll_seconds: int = SCHEDULER_POLL_SECONDS) -> None:
    while True:
        try:
            await asyncio.to_thread(run_due_schedule)
        except Exception:
            logger.exception("Daily schedule check failed")
        await asyncio.sleep(poll_seconds)
