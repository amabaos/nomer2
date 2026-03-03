from typing import List, Dict, Any
import requests
from src.core.config import settings


async def send_lead_html(
    *,
    chat_id: int,
    text_html: str,
    buttons: List[List[Dict[str, Any]]],
):
    """
    Отправка лида через Telegram Bot API.

    chat_id    — куда отправляем (SERVICE_CHAT_ID)
    text_html  — текст сообщения
    buttons    — список строк кнопок:
                  [
                      [{"text": "...", "url": "..."}],
                      [{"text": "...", "callback_data": "..."}],
                  ]
    """

    keyboard = None

    if buttons:
        inline_keyboard = []

        for row in buttons:
            row_buttons = []

            for b in row:
                btn = {"text": b["text"]}

                # Если это URL-кнопка
                if "url" in b:
                    btn["url"] = b["url"]

                # Если это callback-кнопка
                if "callback_data" in b:
                    btn["callback_data"] = b["callback_data"]

                row_buttons.append(btn)

            inline_keyboard.append(row_buttons)

        keyboard = {"inline_keyboard": inline_keyboard}

    payload = {
        "chat_id": chat_id,
        "text": text_html,
        "disable_web_page_preview": True,
    }

    if keyboard:
        payload["reply_markup"] = keyboard

    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"

    resp = requests.post(url, json=payload, timeout=15)

    if not resp.ok:
        print("Ошибка отправки лида через бота:", resp.status_code, resp.text)