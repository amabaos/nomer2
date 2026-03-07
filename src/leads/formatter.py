from typing import Dict, List, Optional


def build_lead_message(
    *,
    chat_title: str,
    chat_username: Optional[str],
    chat_id: int,
    author_username: Optional[str],
    stage: str,
    message_text: str,
    message_id: int,
    matched_groups: Optional[List[str]] = None,
    matched_keyword: Optional[str] = None,
    parser_account: Optional[str] = None,
) -> Dict[str, object]:
    lines: List[str] = ["📥 Новый лид"]

    if parser_account:
        lines.append(f"🤖 Аккаунт: {parser_account}")

    chat_link = f"https://t.me/{chat_username}" if chat_username else None
    if chat_link:
        lines.append(f"💬 Чат: {chat_title} ({chat_link})")
    else:
        lines.append(f"💬 Чат: {chat_title} (приватный)")

    if author_username:
        lines.append(f"👤 Автор: @{author_username}")
        profile_link = f"https://t.me/{author_username}"
    else:
        lines.append("👤 Автор: неизвестен")
        profile_link = None

    if stage:
        lines.append(f"📂 Группа: {stage}")

    if matched_groups and len(matched_groups) > 1:
        lines.append("⚠️ Совпадения ещё в группах: " + ", ".join(matched_groups[1:]))

    if matched_keyword:
        lines.append(f"🔑 Слово-триггер: {matched_keyword}")

    lines.append("📝 Сообщение:")
    lines.append(message_text or "")

    buttons: List[Dict[str, str]] = []
    if profile_link:
        buttons.append({"text": "👤 Профиль", "url": profile_link})

    return {"text": "\n".join(lines), "buttons": buttons}
