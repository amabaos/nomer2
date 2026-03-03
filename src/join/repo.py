from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.join.models import JoinTask

JOIN_QUEUE_FILE = Path("data/users/1/join_queue.json")


def _ensure_file() -> None:
    JOIN_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not JOIN_QUEUE_FILE.exists():
        JOIN_QUEUE_FILE.write_text("[]", encoding="utf-8")


def _parse_task(raw: dict) -> JoinTask:
    data = dict(raw)
    # совместимость со старой схемой очереди
    if data.get("status") == "done":
        data["status"] = "joined"
    if data.get("error") and not data.get("last_error"):
        data["last_error"] = str(data.get("error"))
    if data.get("picked_by_account") is not None and data.get("assigned_account_id") is None:
        data["assigned_account_id"] = data.get("picked_by_account")
    if data.get("chat_id") is not None and data.get("resolved_chat_id") is None:
        data["resolved_chat_id"] = data.get("chat_id")
    if data.get("chat_username") and data.get("resolved_username") is None:
        data["resolved_username"] = data.get("chat_username")
    if data.get("chat_title") and data.get("resolved_title") is None:
        data["resolved_title"] = data.get("chat_title")

    if data.get("updated_at") and isinstance(data["updated_at"], (int, float)):
        data["updated_at"] = datetime.utcfromtimestamp(data["updated_at"])

    # в старых записях id мог быть int
    data["id"] = str(data.get("id"))
    return JoinTask(**data)


def _task_to_dict(task: JoinTask) -> dict:
    payload = task.model_dump(mode="json")
    # legacy-поля для совместимости с существующими файлами/скриптами
    payload["error"] = payload.get("last_error")
    payload["chat_id"] = payload.get("resolved_chat_id")
    payload["chat_username"] = payload.get("resolved_username")
    payload["chat_title"] = payload.get("resolved_title")
    payload["picked_by_account"] = payload.get("assigned_account_id")
    return payload


def load_tasks() -> List[JoinTask]:
    _ensure_file()
    raw = JOIN_QUEUE_FILE.read_text(encoding="utf-8") or "[]"
    data = json.loads(raw)
    if not isinstance(data, list):
        return []

    out: List[JoinTask] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            out.append(_parse_task(item))
        except Exception:
            continue
    return out


def save_tasks(tasks: List[JoinTask]) -> None:
    _ensure_file()
    JOIN_QUEUE_FILE.write_text(
        json.dumps([_task_to_dict(t) for t in tasks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_tasks(tasks: List[JoinTask]) -> int:
    existing = load_tasks()
    seen = {(t.group, t.handle) for t in existing}

    added = 0
    for task in tasks:
        key = (task.group, task.handle)
        if key in seen:
            continue
        existing.append(task)
        seen.add(key)
        added += 1

    save_tasks(existing)
    return added


def pick_next_task(account_id: int) -> Optional[JoinTask]:
    tasks = load_tasks()
    now = datetime.utcnow()

    for task in tasks:
        if task.status != "queued":
            continue
        if task.next_try_at and task.next_try_at > now:
            continue

        task.status = "in_progress"
        task.assigned_account_id = int(account_id)
        task.tries += 1
        task.updated_at = now
        save_tasks(tasks)
        return task

    return None


def mark_join_result(
    task_id: str,
    *,
    status: str,
    chat_id: Optional[int] = None,
    username: Optional[str] = None,
    title: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    tasks = load_tasks()
    now = datetime.utcnow()

    for task in tasks:
        if str(task.id) != str(task_id):
            continue
        task.status = status
        task.updated_at = now
        task.resolved_chat_id = int(chat_id) if chat_id is not None else task.resolved_chat_id
        task.resolved_username = username if username is not None else task.resolved_username
        task.resolved_title = title if title is not None else task.resolved_title
        task.last_error = (str(error)[:500] if error else None)
        break

    save_tasks(tasks)


def mark_done(task_id: str, chat_id: int, username: Optional[str], title: Optional[str]) -> None:
    mark_join_result(task_id, status="joined", chat_id=chat_id, username=username, title=title)


def mark_failed(task_id: str, error: str) -> None:
    mark_join_result(task_id, status="failed", error=error)


def mark_floodwait(task_id: str, seconds: int) -> None:
    tasks = load_tasks()
    now = datetime.utcnow()
    retry_at = datetime.utcfromtimestamp(now.timestamp() + int(seconds) + 2)

    for task in tasks:
        if str(task.id) != str(task_id):
            continue
        task.status = "floodwait"
        task.updated_at = now
        task.next_try_at = retry_at
        task.last_error = f"FloodWait {seconds}s"
        break

    save_tasks(tasks)


def reset_all_to_queued() -> int:
    tasks = load_tasks()
    updated = 0
    now = datetime.utcnow()
    for task in tasks:
        if task.status in {"in_progress", "floodwait", "failed"}:
            task.status = "queued"
            task.updated_at = now
            task.next_try_at = None
            task.last_error = None
            updated += 1
    save_tasks(tasks)
    return updated


def stats() -> Dict[str, int]:
    tasks = load_tasks()
    result: Dict[str, int] = {"total": len(tasks)}
    for st in ["queued", "in_progress", "joined", "already", "pending_request", "floodwait", "failed"]:
        result[st] = sum(1 for t in tasks if t.status == st)
    return result
