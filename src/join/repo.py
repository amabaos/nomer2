import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.join.models import JoinTask

JOIN_QUEUE_FILE = Path("data/users/1/join_queue.json")
_VALID_STATUSES = {"queued", "in_progress", "joined", "already", "pending_request", "failed", "floodwait"}


def _ensure_file() -> None:
    JOIN_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not JOIN_QUEUE_FILE.exists():
        JOIN_QUEUE_FILE.write_text("[]", encoding="utf-8")


def _dt(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _normalize_status(s: str) -> str:
    s = (s or "queued").strip().lower()
    if s == "done":
        return "joined"
    return s if s in _VALID_STATUSES else "queued"


def _normalize_task(raw: dict) -> dict:
    t = dict(raw or {})
    t["id"] = str(t.get("id") or "")
    t["status"] = _normalize_status(t.get("status"))

    # backward compatibility fields
    if t.get("picked_by_account") and not t.get("assigned_account_id"):
        t["assigned_account_id"] = t.get("picked_by_account")

    t.setdefault("group", None)
    t.setdefault("assigned_account_id", None)
    t.setdefault("tries", 0)
    t.setdefault("next_try_at", None)
    t.setdefault("resolved_chat_id", t.get("chat_id"))
    t.setdefault("resolved_title", t.get("chat_title"))
    t.setdefault("resolved_username", t.get("chat_username"))
    t.setdefault("last_error", t.get("error"))
    t.setdefault("updated_at", datetime.utcnow().isoformat())
    return t


def load_tasks() -> List[Dict]:
    _ensure_file()
    raw = JOIN_QUEUE_FILE.read_text(encoding="utf-8") or "[]"
    data = json.loads(raw)
    if not isinstance(data, list):
        return []
    return [_normalize_task(x) for x in data]


def save_tasks(tasks: List[Dict]) -> None:
    _ensure_file()
    normalized = []
    for t in tasks:
        nt = _normalize_task(t)
        nt["updated_at"] = _dt(nt.get("updated_at"))
        nt["next_try_at"] = _dt(nt.get("next_try_at"))
        normalized.append(nt)
    JOIN_QUEUE_FILE.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")


def add_tasks(tasks: List[JoinTask]) -> int:
    existing = load_tasks()
    keys = {(t.get("group"), t.get("handle")) for t in existing}
    added = 0

    for jt in tasks:
        data = jt.model_dump()
        key = (data.get("group"), data.get("handle"))
        if key in keys:
            continue
        data["updated_at"] = _dt(data.get("updated_at"))
        data["next_try_at"] = _dt(data.get("next_try_at"))
        existing.append(_normalize_task(data))
        keys.add(key)
        added += 1

    save_tasks(existing)
    return added


def pick_next_task(account_id: int) -> Optional[Dict]:
    tasks = load_tasks()
    for t in tasks:
        if t.get("status") == "queued":
            t["status"] = "in_progress"
            t["updated_at"] = datetime.utcnow().isoformat()
            t["assigned_account_id"] = int(account_id)
            t["tries"] = int(t.get("tries") or 0) + 1
            save_tasks(tasks)
            return t
    return None


def mark_done(task_id: str, chat_id: int, username: Optional[str], title: Optional[str], status: str = "joined") -> None:
    tasks = load_tasks()
    for t in tasks:
        if str(t.get("id")) == str(task_id):
            t["status"] = _normalize_status(status)
            t["updated_at"] = datetime.utcnow().isoformat()
            t["resolved_chat_id"] = int(chat_id)
            t["resolved_username"] = username
            t["resolved_title"] = title
            t["last_error"] = None
            break
    save_tasks(tasks)


def mark_failed(task_id: str, error: str, *, status: str = "failed") -> None:
    tasks = load_tasks()
    for t in tasks:
        if str(t.get("id")) == str(task_id):
            t["status"] = _normalize_status(status)
            t["updated_at"] = datetime.utcnow().isoformat()
            t["last_error"] = str(error)[:500]
            break
    save_tasks(tasks)


def reset_all_to_queued() -> int:
    tasks = load_tasks()
    touched = 0
    for t in tasks:
        if t.get("status") in {"in_progress", "failed", "floodwait"}:
            t["status"] = "queued"
            t["updated_at"] = datetime.utcnow().isoformat()
            touched += 1
    save_tasks(tasks)
    return touched


def stats() -> Dict[str, int]:
    tasks = load_tasks()
    result = {"total": len(tasks)}
    for key in ["queued", "in_progress", "joined", "already", "pending_request", "floodwait", "failed"]:
        result[key] = sum(1 for t in tasks if t.get("status") == key)
    return result
