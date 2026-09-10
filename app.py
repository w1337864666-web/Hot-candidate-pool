from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from hotspot_agent.config import (
    DEFAULT_PROFILE,
    get_ai_settings,
    get_api_settings,
    get_schedule_settings,
    load_local_env,
)
from hotspot_agent.core import AccountProfile
from hotspot_agent.chat import parse_chat_request, plan_summary
from hotspot_agent.chat_tasks import run_chat_task
from hotspot_agent.chat_store import ChatStore
from hotspot_agent.pipeline import run_scan
from hotspot_agent.scheduler import scheduler_loop
from hotspot_agent.sources import DOMESTIC_SOURCES
from hotspot_agent.storage import SOURCE_ORIGIN_LABELS, Store


load_local_env()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler_task = asyncio.create_task(scheduler_loop())
    try:
        yield
    finally:
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler_task


app = FastAPI(
    title="每日热点到候选内容池 Agent",
    version="0.5.0",
    lifespan=lifespan,
)
BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "hotspot_agent" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "hotspot_agent" / "templates")


STATUS_LABELS = {
    "all": "全部热点",
    "pending": "候选一",
    "final": "候选二",
    "ai_filtered": "AI 未通过",
    "approved": "已采用",
    "edited": "已修改",
    "rejected": "人工已驳回",
    "deleted": "已删除",
}
MODE_LABELS = {"demo": "演示快照", "live": "海外 RSS / Atom", "domestic": "国内 RSS / Atom"}
CANDIDATE_ORIGIN_ORDER = ("domestic", "overseas", "demo")
NAV_DEFINITIONS = (
    ("all", "全部热点", "/candidates"),
    ("pending", "候选一 · AI 初选", "/candidates?status=pending"),
    ("final", "候选二 · 最终池", "/candidates?status=final"),
    ("ai_filtered", "AI 未通过", "/candidates?status=ai_filtered"),
    ("rejected", "人工已驳回", "/candidates?status=rejected"),
    ("deleted", "已删除", "/candidates?status=deleted"),
    ("runs", "运行记录", "/runs"),
)
DEFAULT_PROFILE_FIELDS = {
    "audience": "英语市场的 AI 用户、创作者和产品团队",
    "focus_topics": "AI products, agents, developer tools, creator workflows",
    "avoid_topics": "",
    "tone": "简洁、专业、有明确观点，不过度宣传",
    "free_text": DEFAULT_PROFILE,
}

class ChatMessageRequest(BaseModel):
    message: str
    session_id: str | None = None
    profile: dict[str, str] | None = None


def chat_task_payload(task: dict[str, object]) -> dict[str, object]:
    return {
        key: task.get(key)
        for key in (
            "task_id", "session_id", "status", "plan", "fallback_notice",
            "progress", "progress_label", "run_id", "result", "error",
            "created_at", "started_at", "completed_at",
        )
    }


def _force_demo_on_fallback(plan: dict[str, object], used_model: bool) -> dict[str, object]:
    normalized = dict(plan)
    if not used_model:
        normalized["source_mode"] = "demo"
    return normalized


def status_text(value: str) -> str:
    return STATUS_LABELS.get(value, value)


def mode_text(value: str) -> str:
    return MODE_LABELS.get(value, value)


def navigation_context(store: Store, active_nav: str) -> dict[str, object]:
    counts = store.status_counts()
    api_settings = get_api_settings(store.app_settings())
    model_active = bool(
        api_settings["model_enabled"]
        and api_settings["model_api_key"]
        and (
            api_settings["model_scoring_enabled"]
            or api_settings["model_content_enabled"]
            or api_settings["model_review_enabled"]
        )
    )
    data_active = bool(api_settings["data_api_enabled"] and api_settings["data_api_key"])
    schedule_active = bool(get_schedule_settings(store.app_settings())["schedule_enabled"])
    active_count = int(model_active) + int(data_active) + int(schedule_active)
    active_names = [
        name for name, enabled in (
            ("智能整理与审核", model_active),
            ("数据补充", data_active),
            ("每日自动扫描", schedule_active),
        ) if enabled
    ]
    return {
        "active_nav": active_nav,
        "pending_count": counts["pending"],
        "api_status_label": f"{active_count} 项能力已启用" if active_count else "尚未配置",
        "api_status_note": (
            " · ".join(active_names) if active_names
            else "配置模型与数据检索密钥"
        ),
        "nav_items": [
            {"key": key, "label": label, "href": href, "count": counts.get(key)}
            for key, label, href in NAV_DEFINITIONS
        ],
        "workflow_counts": counts,
    }


def _settings_view(settings: dict[str, object]) -> dict[str, object]:
    return {
        "model_key_configured": bool(settings["model_api_key"]),
        "model_base_url": settings["model_base_url"],
        "model_name": settings["model_name"],
        "model_enabled": settings["model_enabled"],
        "model_scoring_enabled": settings["model_scoring_enabled"],
        "model_content_enabled": settings["model_content_enabled"],
        "model_review_enabled": settings["model_review_enabled"],
        "model_candidate_limit": settings["model_candidate_limit"],
        "model_review_threshold": settings["model_review_threshold"],
        "data_key_configured": bool(settings["data_api_key"]),
        "data_api_enabled": settings["data_api_enabled"],
        "schedule_enabled": settings["schedule_enabled"],
        "schedule_time": settings["schedule_time"],
        "schedule_mode": settings["schedule_mode"],
    }


def _validate_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="模型 API 地址必须是有效的 HTTP/HTTPS URL")
    return normalized


def candidate_origin_context(rows: list[dict], status: str, origin: str) -> dict[str, object]:
    active_origin = origin if origin in {"all", *CANDIDATE_ORIGIN_ORDER} else "all"
    normalized_status = status if status in {*STATUS_LABELS, "all"} else "all"
    counts = {
        key: sum(1 for row in rows if row["source_origin"] == key)
        for key in CANDIDATE_ORIGIN_ORDER
    }
    filtered_rows = rows if active_origin == "all" else [
        row for row in rows if row["source_origin"] == active_origin
    ]
    visible_origins = CANDIDATE_ORIGIN_ORDER if active_origin == "all" else (active_origin,)
    groups = [
        {
            "key": key,
            "label": SOURCE_ORIGIN_LABELS[key],
            "items": [row for row in filtered_rows if row["source_origin"] == key],
        }
        for key in visible_origins
        if any(row["source_origin"] == key for row in filtered_rows)
    ]
    filters = [
        {
            "key": key,
            "label": "全部" if key == "all" else SOURCE_ORIGIN_LABELS[key],
            "count": len(rows) if key == "all" else counts[key],
            "href": f"/candidates?status={normalized_status}&origin={key}",
        }
        for key in ("all", *CANDIDATE_ORIGIN_ORDER)
    ]
    return {
        "candidates": filtered_rows,
        "candidate_groups": groups,
        "candidate_total": len(rows),
        "origin_filters": filters,
        "active_origin": active_origin,
    }


def scan_feedback(run: dict | None) -> dict[str, object] | None:
    """Build a short, actionable message for the scan that just completed."""
    if not run:
        return None
    if run["mode"] == "demo":
        return {
            "kind": "demo",
            "icon": "activity",
            "title": "已载入演示快照",
            "message": "本次使用固定离线数据，不代表刚刚完成了真实 RSS / Atom 抓取。",
            "source_summary": "媒体新闻、官方公告、社区趋势",
        }

    source_scope = "国内" if run["mode"] == "domestic" else "海外"
    stats = run.get("source_stats") or []
    failed = [str(item.get("source_name", "未知来源")) for item in stats if item.get("status") != "ok"]
    succeeded = len(stats) - len(failed)
    error_text = " ".join(str(error) for error in run.get("errors") or [])
    if run.get("fallback_used"):
        if "10013" in error_text:
            reason = "网络层拒绝了出站连接（WinError 10013），请检查 VPN、系统防火墙或代理设置。"
        else:
            reason = "来源没有返回可用条目，请检查网络、来源地址或 RSS/Atom 响应。"
        return {
            "kind": "fallback",
            "icon": "warning",
            "title": "真实扫描未获取到数据，已回退演示快照",
            "message": f"已尝试 {len(stats)} 个{source_scope} RSS / Atom 来源，但没有可用输入。{reason} 当前候选来自固定演示数据。",
            "source_summary": "、".join(failed) or "全部来源",
        }
    if failed:
        return {
            "kind": "partial",
            "icon": "warning",
            "title": "真实扫描完成，但有来源失败",
            "message": f"已获取 {succeeded} 个来源；失败来源不会阻断本轮候选生成。",
            "source_summary": "、".join(failed),
        }
    return {
        "kind": "success",
        "icon": "activity",
        "title": f"{source_scope} RSS / Atom 扫描完成",
        "message": f"已从 {len(stats)} 个{source_scope}来源获取输入并生成候选。",
        "source_summary": "、".join(str(item.get("source_name", "")) for item in stats),
    }


@app.get("/", response_class=HTMLResponse)
def home() -> RedirectResponse:
    return RedirectResponse("/candidates", status_code=303)


@app.get("/settings/api", response_class=HTMLResponse)
def api_settings_page(request: Request, saved: bool = False) -> HTMLResponse:
    with Store() as store:
        stored = store.app_settings()
        settings = {**get_api_settings(stored), **get_schedule_settings(stored)}
        navigation = navigation_context(store, "settings")
    return templates.TemplateResponse(
        request=request,
        name="api_settings.html",
        context={"settings": _settings_view(settings), "saved": saved, **navigation},
    )


@app.post("/settings/api")
def save_api_settings(
    model_api_key: str = Form(""),
    model_base_url: str = Form(""),
    model_name: str = Form("gpt-4o-mini"),
    model_enabled: bool = Form(False),
    model_scoring_enabled: bool = Form(False),
    model_content_enabled: bool = Form(False),
    model_review_enabled: bool = Form(False),
    model_candidate_limit: int = Form(12),
    model_review_threshold: int = Form(70),
    clear_model_api_key: bool = Form(False),
    data_api_key: str = Form(""),
    data_api_enabled: bool = Form(False),
    clear_data_api_key: bool = Form(False),
    schedule_enabled: bool = Form(False),
    schedule_time: str = Form("09:00"),
    schedule_mode: str = Form("live"),
) -> RedirectResponse:
    if not 1 <= model_candidate_limit <= 20:
        raise HTTPException(status_code=400, detail="模型评估数量必须在 1 到 20 之间")
    if not 50 <= model_review_threshold <= 95:
        raise HTTPException(status_code=400, detail="智能审核置信度门槛必须在 50 到 95 之间")
    normalized_model = model_name.strip()
    if not normalized_model:
        raise HTTPException(status_code=400, detail="模型名称不能为空")
    normalized_base_url = _validate_base_url(model_base_url)
    try:
        normalized_schedule_time = datetime.strptime(schedule_time.strip(), "%H:%M").strftime("%H:%M")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="每日扫描时间必须使用 HH:MM 格式") from exc
    if schedule_mode not in {"demo", "live", "domestic"}:
        raise HTTPException(status_code=400, detail="不支持的自动扫描来源模式")
    with Store() as store:
        current = get_api_settings(store.app_settings())
        retained_model_key = "" if clear_model_api_key else model_api_key.strip() or str(current["model_api_key"])
        retained_data_key = "" if clear_data_api_key else data_api_key.strip() or str(current["data_api_key"])
        store.save_app_settings({
            "model_api_key": retained_model_key,
            "model_base_url": normalized_base_url,
            "model_name": normalized_model,
            "model_enabled": int(model_enabled),
            "model_scoring_enabled": int(model_scoring_enabled),
            "model_content_enabled": int(model_content_enabled),
            "model_review_enabled": int(model_review_enabled),
            "model_candidate_limit": model_candidate_limit,
            "model_review_threshold": model_review_threshold,
            "data_api_key": retained_data_key,
            "data_api_enabled": int(data_api_enabled),
            "schedule_enabled": int(schedule_enabled),
            "schedule_time": normalized_schedule_time,
            "schedule_mode": schedule_mode,
        })
    return RedirectResponse("/settings/api?saved=1", status_code=303)


@app.get("/candidates", response_class=HTMLResponse)
def candidates_page(
    request: Request,
    status: str = "all",
    origin: str = "all",
    scan: int | None = None,
) -> HTMLResponse:
    with Store() as store:
        rows = store.list_candidates(status)
        origin_context = candidate_origin_context(rows, status, origin)
        navigation = navigation_context(store, status if status in STATUS_LABELS else "all")
        completed_run = next((run for run in store.list_runs() if run["run_id"] == scan), None) if scan else None
    return templates.TemplateResponse(
        request=request,
        name="candidates.html",
        context={
            "status": status,
            "status_text": status_text,
            "profile_fields": DEFAULT_PROFILE_FIELDS,
            "page_title": "热点工作流" if status in {"all", "全部"} else status_text(status),
            "scan_feedback": scan_feedback(completed_run),
            **origin_context,
            **navigation,
        },
    )


@app.get("/review/next")
def next_review() -> RedirectResponse:
    with Store() as store:
        candidate_id = store.next_pending_candidate_id()
    target = f"/candidates/{candidate_id}" if candidate_id else "/candidates?status=pending"
    return RedirectResponse(target, status_code=303)


@app.get("/candidates/{candidate_id}", response_class=HTMLResponse)
def candidate_detail(request: Request, candidate_id: str) -> HTMLResponse:
    with Store() as store:
        candidate = store.get_candidate(candidate_id)
        active_stage = (
            "deleted" if candidate and candidate["deleted_at"]
            else "final" if candidate and candidate["workflow_stage"] == "final"
            else "pending" if candidate and candidate["workflow_stage"] == "first"
            else "ai_filtered" if candidate and candidate["workflow_stage"] == "ai_filtered"
            else "rejected" if candidate and candidate["workflow_stage"] == "rejected"
            else "all"
        )
        navigation = navigation_context(
            store,
            active_stage,
        )
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return templates.TemplateResponse(
        request=request,
        name="candidate_detail.html",
        context={"candidate": candidate, "status_text": status_text, **navigation},
    )


@app.post("/api/chat/messages")
def chat_message(payload: ChatMessageRequest) -> dict[str, object]:
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="请输入需要抓取或整理的需求")
    with Store() as store:
        settings = get_ai_settings(store.app_settings())
    try:
        parsed = parse_chat_request(payload.message, profile=payload.profile, settings=settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    plan = _force_demo_on_fallback(parsed.plan, parsed.used_model)
    notice = parsed.fallback_notice
    reply = plan_summary(plan)
    if notice:
        reply = f"{reply} {notice}"
    with ChatStore() as chat_store:
        session_id = chat_store.create_session(payload.session_id)
        user_message_id = chat_store.save_message(session_id, "user", payload.message)
        task_id = chat_store.create_task(session_id, user_message_id, plan, notice)
        chat_store.save_message(session_id, "assistant", reply, plan)
    return {
        "session_id": session_id,
        "task_id": task_id,
        "status": "awaiting_confirmation",
        "reply": reply,
        "plan": plan,
        "fallback_notice": notice,
    }


@app.post("/api/tasks/{task_id}/confirm")
def confirm_chat_task(task_id: str) -> dict[str, object]:
    with ChatStore() as chat_store:
        task = chat_store.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Chat task not found")
        if task["status"] == "awaiting_confirmation":
            claimed = chat_store.claim_task(task_id)
        else:
            claimed = False
        task = chat_store.get_task(task_id) or task
    if claimed:
        threading.Thread(target=run_chat_task, args=(task_id,), daemon=True).start()
    elif task["status"] not in {"queued", "running", "succeeded", "partial_success"}:
        raise HTTPException(status_code=409, detail=f"任务当前状态为 {task['status']}，不能确认执行")
    return chat_task_payload(task)


@app.get("/api/tasks/{task_id}")
def get_chat_task(task_id: str) -> dict[str, object]:
    with ChatStore() as chat_store:
        task = chat_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Chat task not found")
    return chat_task_payload(task)


@app.get("/api/chat/sessions/{session_id}/messages")
def get_chat_messages(session_id: str) -> dict[str, object]:
    with ChatStore() as chat_store:
        return {"session_id": session_id, "messages": chat_store.list_messages(session_id)}

@app.post("/scan")
def scan(
    mode: str = Form("demo"),
    profile: str = Form(""),
    audience: str = Form(DEFAULT_PROFILE_FIELDS["audience"]),
    focus_topics: str = Form(DEFAULT_PROFILE_FIELDS["focus_topics"]),
    avoid_topics: str = Form(DEFAULT_PROFILE_FIELDS["avoid_topics"]),
    tone: str = Form(DEFAULT_PROFILE_FIELDS["tone"]),
    profile_notes: str = Form(""),
) -> RedirectResponse:
    account_profile = AccountProfile.from_form(
        audience=audience,
        focus_topics=focus_topics,
        avoid_topics=avoid_topics,
        tone=tone,
        free_text=profile or profile_notes or DEFAULT_PROFILE,
    )
    configs = DOMESTIC_SOURCES if mode == "domestic" else None
    run_id, _errors, _candidates = run_scan(mode, account_profile, configs=configs)
    return RedirectResponse(f"/candidates?scan={run_id}", status_code=303)


@app.post("/candidates/{candidate_id}/review")
def review_candidate(
    candidate_id: str,
    status: str = Form(...),
    english_copy: str = Form(...),
    note: str = Form(""),
    next_action: str = Form("stay"),
) -> RedirectResponse:
    if next_action not in {"next", "stay"}:
        raise HTTPException(status_code=400, detail="unsupported next action")
    with Store() as store:
        try:
            store.review(candidate_id, status, english_copy, note)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    target = "/review/next" if next_action == "next" else f"/candidates/{candidate_id}"
    return RedirectResponse(target, status_code=303)


@app.post("/candidates/{candidate_id}/delete")
def delete_candidate(candidate_id: str) -> RedirectResponse:
    with Store() as store:
        try:
            store.delete_approved(candidate_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Candidate not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/candidates?status=final", status_code=303)


@app.post("/candidates/{candidate_id}/restore")
def restore_candidate(candidate_id: str) -> RedirectResponse:
    with Store() as store:
        try:
            store.restore(candidate_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Candidate not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/candidates?status=deleted", status_code=303)


@app.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request) -> HTMLResponse:
    with Store() as store:
        runs = store.list_runs()
        scheduled_scans = store.list_scheduled_scans()
        navigation = navigation_context(store, "runs")
    return templates.TemplateResponse(
        request=request,
        name="runs.html",
        context={
            "runs": runs,
            "scheduled_scans": scheduled_scans,
            "mode_text": mode_text,
            **navigation,
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "hotspot-agent"}
