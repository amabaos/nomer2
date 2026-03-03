import os
import json
from typing import Dict, List, Set, Tuple
from pathlib import Path
from loguru import logger

GROUPS_FILE = Path("data/users/1/groups.json")


def load_groups_raw() -> list[dict]:
    if not GROUPS_FILE.exists():
        logger.warning("Файл groups.json не найден. Создай группы.")
        return []
    return json.loads(GROUPS_FILE.read_text(encoding="utf-8"))


def collect_active_chat_ids() -> List[int]:
    """
    Собирает уникальный список chat_id из всех enabled групп, где чат active=True.
    """
    groups = load_groups_raw()
    chat_ids: Set[int] = set()

    for g in groups:
        if not g.get("enabled", True):
            continue
        for c in g.get("chats", []) or []:
            if c.get("active", True):
                chat_ids.add(int(c["id"]))

    return sorted(chat_ids)


def build_chat_to_groups_map() -> Dict[int, List[str]]:
    """
    Возвращает mapping: chat_id -> [group_name1, group_name2...]
    (нужно будет воркеру, чтобы понимать, какие группы проверять для конкретного чата)
    """
    groups = load_groups_raw()
    m: Dict[int, List[str]] = {}

    for g in groups:
        if not g.get("enabled", True):
            continue
        name = g.get("name")
        if not name:
            continue
        for c in g.get("chats", []) or []:
            if not c.get("active", True):
                continue
            cid = int(c["id"])
            m.setdefault(cid, []).append(name)

    return m


def build_group_keywords_map() -> Dict[str, List[str]]:
    """
    group_name -> keywords (lowercase)
    """
    groups = load_groups_raw()
    out: Dict[str, List[str]] = {}

    for g in groups:
        if not g.get("enabled", True):
            continue
        name = g.get("name")
        if not name:
            continue
        kws = [str(x).strip().lower() for x in (g.get("keywords") or []) if str(x).strip()]
        out[name] = kws

    return out