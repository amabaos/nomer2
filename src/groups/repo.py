import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

# Пока жёстко под MVP: один владелец user_id=1
GROUPS_PATH = os.path.join("data", "users", "1", "groups.json")


def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(GROUPS_PATH), exist_ok=True)


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _norm_keyword_item(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return None
        return {"text": text, "active": True, "added_at": _now_iso()}

    if isinstance(value, dict):
        text = str(value.get("text") or "").strip().lower()
        if not text:
            return None
        return {
            "text": text,
            "active": bool(value.get("active", True)),
            "added_at": value.get("added_at") or _now_iso(),
        }
    return None


def _normalize_group(group: Dict[str, Any]) -> Dict[str, Any]:
    g = dict(group or {})
    g["name"] = (g.get("name") or "").strip()
    g["enabled"] = bool(g.get("enabled", True))

    norm_keywords: List[Dict[str, Any]] = []
    seen = set()
    for kw in (g.get("keywords") or []):
        item = _norm_keyword_item(kw)
        if not item:
            continue
        key = item["text"]
        if key in seen:
            continue
        seen.add(key)
        norm_keywords.append(item)
    g["keywords"] = norm_keywords

    norm_chats: List[Dict[str, Any]] = []
    for chat in (g.get("chats") or []):
        if not isinstance(chat, dict) or chat.get("id") is None:
            continue
        norm_chats.append(
            {
                "id": int(chat.get("id")),
                "username": chat.get("username"),
                "title": chat.get("title") or "Без названия",
                "active": bool(chat.get("active", True)),
                "added_at": chat.get("added_at") or _now_iso(),
                "added_by_account_id": chat.get("added_by_account_id"),
            }
        )
    g["chats"] = norm_chats
    return g


def load_groups() -> List[Dict[str, Any]]:
    _ensure_dir()
    if not os.path.exists(GROUPS_PATH):
        return []
    with open(GROUPS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [_normalize_group(g) for g in (data or []) if isinstance(g, dict)]


def save_groups(groups: List[Dict[str, Any]]) -> None:
    _ensure_dir()
    normalized = [_normalize_group(g) for g in (groups or [])]
    with open(GROUPS_PATH, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)


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

    updated = _normalize_group(group)
    for i, g in enumerate(groups):
        if g.get("name") == name:
            groups[i] = updated
            save_groups(groups)
            return

    groups.append(updated)
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
    exists = next((x for x in kws if x.get("text") == phrase), None)
    if exists:
        exists["active"] = True
    else:
        kws.append({"text": phrase, "active": True, "added_at": _now_iso()})

    g["keywords"] = kws
    upsert_group(g)


def remove_keyword(group_name: str, phrase: str) -> None:
    g = get_group(group_name)
    if not g:
        raise ValueError("Группа не найдена")

    phrase = (phrase or "").strip().lower()
    kws = [x for x in (g.get("keywords") or []) if x.get("text") != phrase]
    g["keywords"] = kws
    upsert_group(g)


def set_keyword_active(group_name: str, phrase: str, active: bool) -> bool:
    g = get_group(group_name)
    if not g:
        raise ValueError("Группа не найдена")

    phrase = (phrase or "").strip().lower()
    changed = False
    kws = g.get("keywords") or []
    for item in kws:
        if item.get("text") == phrase:
            old = bool(item.get("active", True))
            new = bool(active)
            if old != new:
                item["active"] = new
                changed = True
            break

    if changed:
        g["keywords"] = kws
        upsert_group(g)
    return changed


def add_chat(group_name: str, chat: Dict[str, Any]) -> None:
    g = get_group(group_name)
    if not g:
        raise ValueError("Группа не найдена")

    chats = g.get("chats") or []
    chat_id = chat.get("id")
    if chat_id is None:
        raise ValueError("chat.id обязателен")

    normalized = {
        "id": int(chat_id),
        "username": chat.get("username"),
        "title": chat.get("title") or "Без названия",
        "active": bool(chat.get("active", True)),
        "added_at": chat.get("added_at") or _now_iso(),
        "added_by_account_id": chat.get("added_by_account_id"),
    }

    for c in chats:
        if c.get("id") == int(chat_id):
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

    chats = [c for c in (g.get("chats") or []) if c.get("id") != int(chat_id)]
    g["chats"] = chats
    upsert_group(g)


def set_chat_active(chat_id: int, active: bool) -> int:
    groups = load_groups()
    changed = 0

    for g in groups:
        chats = g.get("chats") or []
        for c in chats:
            if c.get("id") != int(chat_id):
                continue
            old = bool(c.get("active", True))
            new = bool(active)
            if old != new:
                c["active"] = new
                changed += 1

    if changed:
        save_groups(groups)
    return changed



def remove_chat_everywhere(chat_id: int) -> int:
    groups = load_groups()
    removed = 0
    for g in groups:
        before = len(g.get("chats") or [])
        g["chats"] = [c for c in (g.get("chats") or []) if c.get("id") != int(chat_id)]
        removed += max(0, before - len(g["chats"]))
    if removed:
        save_groups(groups)
    return removed
