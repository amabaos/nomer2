import time
import requests
from loguru import logger
from sqlalchemy.exc import IntegrityError

from src.core.config import settings
from src.db.database import SessionLocal
from src.db.models import Blacklist

API = f"https://api.telegram.org/bot{settings.bot_token}"


def _answer_callback(callback_query_id: str, text: str = ""):
    try:
        requests.post(
            f"{API}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text, "show_alert": False},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"answerCallbackQuery error: {e}")


def _set_blacklist(user_id: int, enable: bool) -> bool:
    """
    Возвращает итоговое состояние:
    True  -> пользователь в ЧС
    False -> пользователя нет в ЧС
    """
    db = SessionLocal()
    try:
        if enable:
            try:
                db.add(Blacklist(user_id=user_id))
                db.commit()
            except IntegrityError:
                db.rollback()
            return True
        else:
            db.query(Blacklist).filter(Blacklist.user_id == user_id).delete()
            db.commit()
            return False
    finally:
        db.close()


def _build_toggled_keyboard(existing_keyboard: list, is_blacklisted: bool, user_id: int) -> dict:
    """
    existing_keyboard — это inline_keyboard из сообщения (список строк).
    Мы:
      1) оставляем все строки, кроме строки с нашей кнопкой ЧС
      2) добавляем новую строку с ЧС или Разблокировать
    """
    kept_rows = []

    # 1) оставляем все строки кроме тех, где есть кнопка с callback_data вида bl:on:* / bl:off:*
    for row in existing_keyboard or []:
        has_bl_button = False
        for btn in row:
            cd = btn.get("callback_data")
            if cd and cd.startswith("bl:"):
                has_bl_button = True
                break

        if not has_bl_button:
            kept_rows.append(row)

    # 2) добавляем нашу строку-тумблер
    if is_blacklisted:
        toggle_row = [{"text": "✅ Разблокировать", "callback_data": f"bl:off:{user_id}"}]
    else:
        toggle_row = [{"text": "🚫 В ЧС", "callback_data": f"bl:on:{user_id}"}]

    kept_rows.append(toggle_row)

    return {"inline_keyboard": kept_rows}


def _edit_keyboard(chat_id: int, message_id: int, new_reply_markup: dict):
    try:
        requests.post(
            f"{API}/editMessageReplyMarkup",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": new_reply_markup,
            },
            timeout=10,
        )
    except Exception as e:
        logger.error(f"editMessageReplyMarkup error: {e}")


def run_bot_updates_loop():
    """
    Long-polling getUpdates: слушаем callback_query и переключаем blacklist.
    """
    logger.info("[BOT] Callback loop started")
    offset = 0

    while True:
        try:
            r = requests.get(
                f"{API}/getUpdates",
                params={"timeout": 30, "offset": offset},
                timeout=35,
            )
            data = r.json()

            if not data.get("ok"):
                time.sleep(2)
                continue

            for upd in data.get("result", []):
                offset = upd["update_id"] + 1

                cq = upd.get("callback_query")
                if not cq:
                    continue

                from_id = cq["from"]["id"]
                callback_id = cq["id"]

                # только владелец
                if from_id != settings.owner_id:
                    _answer_callback(callback_id, "Нет доступа")
                    continue

                msg = cq.get("message")
                if not msg:
                    _answer_callback(callback_id, "Нет сообщения")
                    continue

                chat_id = msg["chat"]["id"]
                message_id = msg["message_id"]

                # текущая клавиатура сообщения (чтобы сохранить url-кнопки)
                existing_keyboard = []
                rm = msg.get("reply_markup")
                if rm and isinstance(rm, dict):
                    existing_keyboard = rm.get("inline_keyboard") or []

                data_str = cq.get("data", "")
                parts = data_str.split(":")
                if len(parts) != 3 or parts[0] != "bl":
                    _answer_callback(callback_id, "Неизвестная кнопка")
                    continue

                action = parts[1]
                user_id = int(parts[2])

                if action == "on":
                    is_bl = _set_blacklist(user_id, True)
                    new_rm = _build_toggled_keyboard(existing_keyboard, is_bl, user_id)
                    _edit_keyboard(chat_id, message_id, new_rm)
                    _answer_callback(callback_id, "Добавил в ЧС ✅")

                elif action == "off":
                    is_bl = _set_blacklist(user_id, False)
                    new_rm = _build_toggled_keyboard(existing_keyboard, is_bl, user_id)
                    _edit_keyboard(chat_id, message_id, new_rm)
                    _answer_callback(callback_id, "Убрал из ЧС ✅")

                else:
                    _answer_callback(callback_id, "Неизвестное действие")

        except Exception as e:
            logger.error(f"[BOT] updates loop error: {e}")
            time.sleep(2)