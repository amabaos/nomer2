import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

# Пока жёстко под MVP: один владелец user_id=1
GROUPS_PATH = os.path.join("data", "users", "1", "groups.json")


def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(GROUPS_PATH), exist_ok=True)


def load_groups() -> List[Dict[str, Any]]:
    _ensure_dir()
    if not os.path.exists(GROUPS_PATH):
        return []
    with open(GROUPS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_groups(groups: List[Dict[str, Any]]) -> None:
    _ensure_dir()
    with open(GROUPS_PATH, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)


def get_group(name: str) -> Optional[Dict[str, Any]]:
    name = (name or "").strip()
    for g in load_groups():
        if g.get("name") == name:
            return g
    return None


def upsert_group(group: Dict[str, Any]) -> None:
    groups = load_groups()
    name = (group.get("name") or "").strip()
    if not name:
        raise ValueError("group.name пуст")

    for i, g in enumerate(groups):
        if g.get("name") == name:
            groups[i] = group
            save_groups(groups)
            return

    groups.append(group)
    save_groups(groups)


def create_group(name: str) -> None:
    name = (name or "").strip()
    if not name:
        raise ValueError("Пустое имя группы")
    if get_group(name):
        raise ValueError("Такая группа уже существует")
    upsert_group({"name": name, "enabled": True, "keywords": [], "chats": []})


def set_group_enabled(name: str, enabled: bool) -> None:
    g = get_group(name)
    if not g:
        raise ValueError("Группа не найдена")
    g["enabled"] = bool(enabled)
    upsert_group(g)


def add_keyword(group_name: str, phrase: str) -> None:
    g = get_group(group_name)
    if not g:
        raise ValueError("Группа не найдена")

    phrase = (phrase or "").strip().lower()
    if not phrase:
        raise ValueError("Пустая фраза")

    kws = g.get("keywords") or []
    if phrase not in kws:
        kws.append(phrase)

    g["keywords"] = kws
    upsert_group(g)


def remove_keyword(group_name: str, phrase: str) -> None:
    g = get_group(group_name)
    if not g:
        raise ValueError("Группа не найдена")

    phrase = (phrase or "").strip().lower()
    kws = [x for x in (g.get("keywords") or []) if x != phrase]
    g["keywords"] = kws
    upsert_group(g)


def add_chat(group_name: str, chat: Dict[str, Any]) -> None:
    g = get_group(group_name)
    if not g:
        raise ValueError("Группа не найдена")

    chats = g.get("chats") or []
    chat_id = chat.get("id")
    if chat_id is None:
        raise ValueError("chat.id обязателен")

    normalized = dict(chat)
    normalized.setdefault("active", True)
    normalized.setdefault("added_at", datetime.utcnow().isoformat())

    for c in chats:
        if c.get("id") == chat_id:
            prev_added_at = c.get("added_at")
            c.update(normalized)
            if prev_added_at:
                c["added_at"] = prev_added_at
            upsert_group(g)
            return

    chats.append(normalized)
    g["chats"] = chats
    upsert_group(g)


def remove_chat(group_name: str, chat_id: int) -> None:
    g = get_group(group_name)
    if not g:
        raise ValueError("Группа не найдена")

    chats = [c for c in (g.get("chats") or []) if c.get("id") != chat_id]
    g["chats"] = chats
    upsert_group(g)


def set_chat_active(chat_id: int, active: bool) -> int:
    groups = load_groups()
    changed = 0

    for g in groups:
        chats = g.get("chats") or []
        for c in chats:
            if c.get("id") != chat_id:
                continue
            old = bool(c.get("active", True))
            new = bool(active)
            if old != new:
                c["active"] = new
                changed += 1

    if changed:
        save_groups(groups)
    return changed
