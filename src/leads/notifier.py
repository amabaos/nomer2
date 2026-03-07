from typing import List, Dict, Any
import asyncio
import requests
from loguru import logger

from src.core.config import settings


async def send_lead_html(
    *,
    chat_id: int,
    text_html: str,
    buttons: List[List[Dict[str, Any]]],
):
    keyboard = None
    if buttons:
        inline_keyboard = []
        for row in buttons:
            row_buttons = []
            for b in row:
                btn = {"text": b["text"]}
                if "url" in b:
                    btn["url"] = b["url"]
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

    attempts = 3
    last_err = None
    for i in range(attempts):
        try:
            resp = await asyncio.to_thread(requests.post, url, json=payload, timeout=15)
            if resp.ok:
                return True
            last_err = f"status={resp.status_code}"
            logger.warning(f"[NOTIFY] sendMessage failed ({i + 1}/{attempts}): {resp.status_code}")
        except Exception as e:
            last_err = str(e)
            logger.warning(f"[NOTIFY] sendMessage exception ({i + 1}/{attempts}): {e}")
        await asyncio.sleep(1 + i)

    logger.error(f"[NOTIFY] lead not sent after retries: {last_err}")
    return False
