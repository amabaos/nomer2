import json
from pathlib import Path
from typing import Dict, List, Set, Iterable
from loguru import logger

ASSIGNMENTS_FILE = Path("data/users/1/assignments.json")


def _ensure_file() -> None:
    ASSIGNMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ASSIGNMENTS_FILE.exists():
        ASSIGNMENTS_FILE.write_text("{}", encoding="utf-8")


def load_assignments() -> Dict[str, List[int]]:
    _ensure_file()
    data = json.loads(ASSIGNMENTS_FILE.read_text(encoding="utf-8") or "{}")
    # нормализуем
    out: Dict[str, List[int]] = {}
    for k, v in (data or {}).items():
        try:
            acc_id = str(int(k))
        except Exception:
            continue
        ids: List[int] = []
        for x in (v or []):
            try:
                ids.append(int(x))
            except Exception:
                pass
        # уникализируем, сохраняя порядок
        seen = set()
        uniq = []
        for cid in ids:
            if cid not in seen:
                uniq.append(cid)
                seen.add(cid)
        out[acc_id] = uniq
    return out


def save_assignments(data: Dict[str, List[int]]) -> None:
    _ensure_file()
    ASSIGNMENTS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_account_chat_ids(account_id: int) -> List[int]:
    data = load_assignments()
    return data.get(str(int(account_id)), [])


def add_chat_to_account(account_id: int, chat_id: int) -> None:
    data = load_assignments()
    key = str(int(account_id))
    chats = data.get(key, [])
    cid = int(chat_id)
    if cid not in chats:
        chats.append(cid)
    data[key] = chats
    save_assignments(data)


def remove_chat_from_account(account_id: int, chat_id: int) -> None:
    data = load_assignments()
    key = str(int(account_id))
    cid = int(chat_id)
    chats = [x for x in data.get(key, []) if int(x) != cid]
    if chats:
        data[key] = chats
    else:
        data.pop(key, None)
    save_assignments(data)


def set_account_chats(account_id: int, chat_ids: Iterable[int]) -> None:
    data = load_assignments()
    key = str(int(account_id))
    seen: Set[int] = set()
    out: List[int] = []
    for x in chat_ids:
        cid = int(x)
        if cid not in seen:
            out.append(cid)
            seen.add(cid)
    data[key] = out
    save_assignments(data)


def auto_assign_chat_ids_round_robin(account_ids: List[int], chat_ids: List[int]) -> Dict[int, List[int]]:
    """
    Возвращает распределение (не сохраняет):
    acc_id -> list[chat_id]
    """
    accs = [int(a) for a in account_ids]
    chats = [int(c) for c in chat_ids]

    if not accs:
        raise ValueError("account_ids пуст")
    if not chats:
        return {a: [] for a in accs}

    result: Dict[int, List[int]] = {a: [] for a in accs}
    i = 0
    for cid in chats:
        a = accs[i % len(accs)]
        result[a].append(cid)
        i += 1
    return result


def apply_distribution(distribution: Dict[int, List[int]], mode: str = "append") -> None:
    """
    mode:
      - append: добавляет к существующим назначениям (не удаляет старые)
      - replace: полностью заменяет назначения для указанных аккаунтов
    """
    data = load_assignments()
    for acc_id, chat_ids in distribution.items():
        key = str(int(acc_id))
        if mode == "replace":
            data[key] = list(dict.fromkeys([int(x) for x in chat_ids]))
        else:
            existing = data.get(key, [])
            seen = set(existing)
            for cid in chat_ids:
                cid = int(cid)
                if cid not in seen:
                    existing.append(cid)
                    seen.add(cid)
            data[key] = existing
    save_assignments(data)