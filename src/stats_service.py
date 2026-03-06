from sqlalchemy import func

from src.accounts.repo import list_accounts
from src.assignments.repo import load_assignments
from src.db.database import SessionLocal
from src.db.models import ProcessedMessage, Blacklist
from src.groups.runtime import collect_active_chat_ids
from src.join.repo import stats as join_stats


def collect_system_stats() -> dict:
    accounts = list_accounts()
    active_accounts = [a for a in accounts if a.status == "active"]
    assignments = load_assignments()
    assigned_chat_ids = set()
    for ids in assignments.values():
        assigned_chat_ids.update(int(x) for x in (ids or []))

    db = SessionLocal()
    try:
        leads_sent = db.query(func.count(ProcessedMessage.id)).scalar() or 0
        blacklisted = db.query(func.count(Blacklist.id)).scalar() or 0
    finally:
        db.close()

    monitored_chat_ids = assigned_chat_ids or set(collect_active_chat_ids())

    return {
        "accounts_total": len(accounts),
        "accounts_active": len(active_accounts),
        "accounts_paused": sum(1 for a in accounts if a.status == "paused"),
        "accounts_disabled": sum(1 for a in accounts if a.status == "disabled"),
        "accounts_error": sum(1 for a in accounts if a.status in {"error", "authorization_required"}),
        "chats_monitored": len(monitored_chat_ids),
        "leads_sent_total": int(leads_sent),
        "blacklist_total": int(blacklisted),
        "join": join_stats(),
    }
