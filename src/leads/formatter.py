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
    # НОВОЕ (опционально)
    matched_groups: Optional[List[str]] = None,
    matched_keyword: Optional[str] = None,
    parser_account: Optional[str] = None,
) -> Dict[str, object]:
    """
    Собирает текст лида + набор кнопок.
    stage — оставили параметром, но теперь туда можно передавать НАЗВАНИЕ ГРУППЫ/СЕГМЕНТА.
    matched_groups — список групп, если совпало несколько (для дебага).
    matched_keyword — какое конкретно слово/фраза сработали.
    """

    lines: List[str] = []
    lines.append("🔔 Новый лид!")
    if parser_account:
        lines.append(f"Аккаунт: {parser_account}")

    # --- ЧАТ ---
    if chat_username:
        chat_line = f"Чат: {chat_title} (https://t.me/{chat_username})"
        msg_link = f"https://t.me/{chat_username}/{message_id}"
    else:
        chat_line = f"Чат: {chat_title}"
        msg_link = None
    lines.append(chat_line)

    # --- АВТОР ---
    if author_username:
        author_tag = f"@{author_username}"
        author_line = f"Автор: {author_tag}"
        profile_link = f"https://t.me/{author_username}"
    else:
        author_line = "Автор: неизвестен"
        profile_link = None
    lines.append(author_line)

    # --- ГРУППА/СТЕЙДЖ ---
    # stage = "группа" (например KYIV_WORK)
    if stage:
        lines.append(f"Группа: {stage}")

    # Если совпало несколько групп — покажем предупреждение
    if matched_groups and len(matched_groups) > 1:
        lines.append("⚠️ Совпало групп: " + ", ".join(matched_groups))

    # Если хотим видеть, какое слово сработало
    if matched_keyword:
        lines.append(f"Триггер: {matched_keyword}")

    # --- СООБЩЕНИЕ ---
    lines.append("")
    lines.append("Сообщение:")
    lines.append(message_text or "")

    body = "\n".join(lines)

    # --- КНОПКИ ---
    # ВАЖНО: возвращаем ПЛОСКИЙ список, а воркер сам превратит в "строки" и добавит ЧС
    buttons: List[Dict[str, str]] = []
    if msg_link:
        buttons.append({"text": "✉️ К сообщению", "url": msg_link})
    if profile_link:
        buttons.append({"text": "👤 Профиль", "url": profile_link})

    return {"text": body, "buttons": buttons}