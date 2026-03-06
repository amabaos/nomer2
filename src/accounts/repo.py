import json
from pathlib import Path
from typing import List, Optional
from src.accounts.models import Account

USERS_DIR = Path("data/users/1")
ACCOUNTS_JSON = USERS_DIR / "accounts.json"

_ALLOWED_STATUSES = {"active", "paused", "disabled", "error", "authorization_required", "inactive"}


def _ensure_dirs():
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    if not ACCOUNTS_JSON.exists():
        ACCOUNTS_JSON.write_text("[]", encoding="utf-8")


def _normalize_account(raw: dict) -> dict:
    data = dict(raw or {})
    status = str(data.get("status", "inactive")).strip().lower()
    data["status"] = status if status in _ALLOWED_STATUSES else "inactive"
    if "status_reason" not in data:
        data["status_reason"] = None
    if "max_chats" not in data:
        data["max_chats"] = 250
    return data


def list_accounts() -> List[Account]:
    _ensure_dirs()
    data = json.loads(ACCOUNTS_JSON.read_text(encoding="utf-8"))
    return [Account(**_normalize_account(a)) for a in data]


def save_accounts(accounts: List[Account]) -> None:
    _ensure_dirs()
    ACCOUNTS_JSON.write_text(
        json.dumps([a.model_dump() for a in accounts], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_next_id(accounts: List[Account]) -> int:
    return max((a.id for a in accounts), default=0) + 1


def get_account(account_id: int) -> Optional[Account]:
    for a in list_accounts():
        if a.id == account_id:
            return a
    return None


def upsert_account(acc: Account) -> None:
    accs = list_accounts()
    updated = False
    for i, a in enumerate(accs):
        if a.id == acc.id:
            accs[i] = acc
            updated = True
            break
    if not updated:
        accs.append(acc)
    save_accounts(accs)
