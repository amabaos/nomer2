import json
from pathlib import Path
from typing import Dict, List, Set

from loguru import logger

GROUPS_FILE = Path("data/users/1/groups.json")


def load_groups_raw() -> list[dict]:
    if not GROUPS_FILE.exists():
        logger.warning("Файл groups.json не найден. Создай группы.")
        return []
    data = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def collect_active_chat_ids() -> List[int]:
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


def _keyword_text(item) -> str:
    if isinstance(item, str):
        return item.strip().lower()
    if isinstance(item, dict):
        if not item.get("active", True):
            return ""
        return str(item.get("text") or "").strip().lower()
    return ""


def build_group_keywords_map() -> Dict[str, List[str]]:
    groups = load_groups_raw()
    out: Dict[str, List[str]] = {}

    for g in groups:
        if not g.get("enabled", True):
            continue
        name = g.get("name")
        if not name:
            continue
        kws = [_keyword_text(x) for x in (g.get("keywords") or [])]
        out[name] = [x for x in kws if x]

    return out
