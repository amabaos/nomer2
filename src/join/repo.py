import json
import time
from pathlib import Path
from typing import Dict, List, Optional

JOIN_QUEUE_FILE = Path("data/users/1/join_queue.json")


def _ensure_file() -> None:
    JOIN_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not JOIN_QUEUE_FILE.exists():
        JOIN_QUEUE_FILE.write_text("[]", encoding="utf-8")


def load_tasks() -> List[Dict]:
    _ensure_file()
    raw = JOIN_QUEUE_FILE.read_text(encoding="utf-8") or "[]"
    data = json.loads(raw)
    return data if isinstance(data, list) else []


def save_tasks(tasks: List[Dict]) -> None:
    _ensure_file()
    JOIN_QUEUE_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")


def enqueue_tasks(group: str, handles: List[str]) -> int:
    tasks = load_tasks()

    # чтобы не добавлять дубли одинаковых handle в той же группе
    existing = {(t.get("group"), t.get("handle")) for t in tasks}

    added = 0
    now = int(time.time())
    next_id = max([int(t.get("id", 0)) for t in tasks] or [0]) + 1

    for h in handles:
        key = (group, h)
        if key in existing:
            continue
        tasks.append({
            "id": next_id,
            "group": group,
            "handle": h,
            "status": "queued",          # queued | in_progress | done | failed
            "created_at": now,
            "updated_at": now,
            "error": None,
            "chat_id": None,
            "chat_username": None,
            "chat_title": None,
            "picked_by_account": None,
        })
        existing.add(key)
        next_id += 1
        added += 1

    save_tasks(tasks)
    return added


def pick_next_task(account_id: int) -> Optional[Dict]:
    tasks = load_tasks()
    now = int(time.time())

    for t in tasks:
        if t.get("status") == "queued":
            t["status"] = "in_progress"
            t["updated_at"] = now
            t["picked_by_account"] = int(account_id)
            save_tasks(tasks)
            return t
    return None


def mark_done(task_id: int, chat_id: int, username: Optional[str], title: Optional[str]) -> None:
    tasks = load_tasks()
    now = int(time.time())
    for t in tasks:
        if int(t.get("id")) == int(task_id):
            t["status"] = "done"
            t["updated_at"] = now
            t["chat_id"] = int(chat_id)
            t["chat_username"] = username
            t["chat_title"] = title
            t["error"] = None
            break
    save_tasks(tasks)


def mark_failed(task_id: int, error: str) -> None:
    tasks = load_tasks()
    now = int(time.time())
    for t in tasks:
        if int(t.get("id")) == int(task_id):
            t["status"] = "failed"
            t["updated_at"] = now
            t["error"] = str(error)[:500]
            break
    save_tasks(tasks)


def stats() -> Dict[str, int]:
    tasks = load_tasks()
    total = len(tasks)
    queued = sum(1 for t in tasks if t.get("status") == "queued")
    in_progress = sum(1 for t in tasks if t.get("status") == "in_progress")
    done = sum(1 for t in tasks if t.get("status") == "done")
    failed = sum(1 for t in tasks if t.get("status") == "failed")
    return {
        "total": total,
        "queued": queued,
        "in_progress": in_progress,
        "done": done,
        "failed": failed,
    }