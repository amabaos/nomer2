from datetime import datetime, timedelta

from sqlalchemy import func

from src.accounts.repo import list_accounts
from src.assignments.repo import load_assignments
from src.db.database import SessionLocal
from src.db.models import ProcessedMessage, Blacklist, LeadEvent
from src.groups.runtime import collect_active_chat_ids
from src.groups.repo import load_groups
from src.join.repo import stats as join_stats


def _keywords_stats(groups: list[dict]) -> tuple[int, int]:
    total = 0
    active = 0
    for g in groups:
        for kw in (g.get("keywords") or []):
            total += 1
            if isinstance(kw, str):
                active += 1
            elif isinstance(kw, dict) and kw.get("active", True):
                active += 1
    return total, active


def collect_system_stats() -> dict:
    accounts = list_accounts()
    active_accounts = [a for a in accounts if a.status == "active"]
    assignments = load_assignments()
    assigned_chat_ids = set()
    for ids in assignments.values():
        assigned_chat_ids.update(int(x) for x in (ids or []))

    groups = load_groups()
    total_groups = len(groups)
    enabled_groups = sum(1 for g in groups if g.get("enabled", True))

    total_chats = set()
    active_chats = set()
    for g in groups:
        for c in (g.get("chats") or []):
            cid = int(c.get("id"))
            total_chats.add(cid)
            if g.get("enabled", True) and c.get("active", True):
                active_chats.add(cid)

    total_keywords, active_keywords = _keywords_stats(groups)

    db = SessionLocal()
    try:
        leads_sent = db.query(func.count(ProcessedMessage.id)).scalar() or 0
        blacklisted = db.query(func.count(Blacklist.id)).scalar() or 0

        now = datetime.utcnow()
        leads_1h = db.query(func.count(LeadEvent.id)).filter(LeadEvent.created_at >= now - timedelta(hours=1)).scalar() or 0
        leads_24h = db.query(func.count(LeadEvent.id)).filter(LeadEvent.created_at >= now - timedelta(hours=24)).scalar() or 0
        leads_7d = db.query(func.count(LeadEvent.id)).filter(LeadEvent.created_at >= now - timedelta(days=7)).scalar() or 0
    finally:
        db.close()

    monitored_chat_ids = assigned_chat_ids or set(collect_active_chat_ids())

    return {
        "accounts_total": len(accounts),
        "accounts_active": len(active_accounts),
        "accounts_paused": sum(1 for a in accounts if a.status == "paused"),
        "accounts_disabled": sum(1 for a in accounts if a.status == "disabled"),
        "accounts_error": sum(1 for a in accounts if a.status in {"error", "authorization_required"}),
        "groups_total": total_groups,
        "groups_enabled": enabled_groups,
        "chats_total": len(total_chats),
        "chats_active": len(active_chats),
        "chats_monitored": len(monitored_chat_ids),
        "keywords_total": total_keywords,
        "keywords_active": active_keywords,
        "leads_sent_total": int(leads_sent),
        "leads_1h": int(leads_1h),
        "leads_24h": int(leads_24h),
        "leads_7d": int(leads_7d),
        "blacklist_total": int(blacklisted),
        "join": join_stats(),
    }
