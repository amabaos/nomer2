import time
import requests
from loguru import logger
from sqlalchemy.exc import IntegrityError

from src.core.config import settings
from src.db.database import SessionLocal
from src.db.models import Blacklist
from src.accounts.repo import list_accounts
from src.groups.repo import load_groups
from src.stats_service import collect_system_stats

API = f"https://api.telegram.org/bot{settings.bot_token}"


def _send_message(chat_id: int, text: str):
    try:
        requests.post(
            f"{API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"sendMessage error: {e}")


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
    db = SessionLocal()
    try:
        if enable:
            try:
                db.add(Blacklist(user_id=user_id))
                db.commit()
            except IntegrityError:
                db.rollback()
            return True
        db.query(Blacklist).filter(Blacklist.user_id == user_id).delete()
        db.commit()
        return False
    finally:
        db.close()


def _build_toggled_keyboard(existing_keyboard: list, is_blacklisted: bool, user_id: int) -> dict:
    kept_rows = []
    for row in existing_keyboard or []:
        has_bl_button = False
        for btn in row:
            cd = btn.get("callback_data")
            if cd and cd.startswith("bl:"):
                has_bl_button = True
                break
        if not has_bl_button:
            kept_rows.append(row)

    toggle_row = [{"text": "✅ Разблокировать", "callback_data": f"bl:off:{user_id}"}] if is_blacklisted else [{"text": "🚫 В ЧС", "callback_data": f"bl:on:{user_id}"}]
    kept_rows.append(toggle_row)
    return {"inline_keyboard": kept_rows}


def _edit_keyboard(chat_id: int, message_id: int, new_reply_markup: dict):
    try:
        requests.post(
            f"{API}/editMessageReplyMarkup",
            json={"chat_id": chat_id, "message_id": message_id, "reply_markup": new_reply_markup},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"editMessageReplyMarkup error: {e}")


def _handle_owner_command(msg: dict) -> bool:
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return False

    chat_id = msg["chat"]["id"]
    cmd = text.split()[0].lower()

    if cmd in {"/start", "/menu", "/help"}:
        _send_message(
            chat_id,
            "MVP меню:\n"
            "/stats — статистика\n"
            "/accounts — аккаунты\n"
            "/groups — группы\n"
            "/ping — проверка",
        )
        return True

    if cmd == "/ping":
        _send_message(chat_id, "pong ✅")
        return True

    if cmd == "/accounts":
        accs = list_accounts()
        if not accs:
            _send_message(chat_id, "Аккаунтов пока нет")
            return True
        rows = [f"#{a.id} | {a.phone} | status={a.status}" for a in accs]
        _send_message(chat_id, "Аккаунты:\n" + "\n".join(rows))
        return True

    if cmd == "/groups":
        groups = load_groups()
        if not groups:
            _send_message(chat_id, "Групп пока нет")
            return True
        rows = [f"{g['name']} | enabled={g.get('enabled', True)} | keywords={len(g.get('keywords', []))} | chats={len(g.get('chats', []))}" for g in groups]
        _send_message(chat_id, "Группы:\n" + "\n".join(rows))
        return True

    if cmd == "/stats":
        s = collect_system_stats()
        text = (
            "Статистика MVP:\n"
            f"Аккаунты: {s['accounts_total']} (active={s['accounts_active']}, paused={s['accounts_paused']}, disabled={s['accounts_disabled']}, errors={s['accounts_error']})\n"
            f"Мониторинг чатов: {s['chats_monitored']}\n"
            f"Найдено/отправлено лидов: {s['leads_sent_total']}\n"
            f"Blacklist: {s['blacklist_total']}\n"
            f"Join queue: total={s['join']['total']} queued={s['join']['queued']} in_progress={s['join']['in_progress']} failed={s['join']['failed']}"
        )
        _send_message(chat_id, text)
        return True

    return False


def run_bot_updates_loop():
    logger.info("[BOT] Callback/commands loop started")
    offset = 0

    while True:
        try:
            r = requests.get(f"{API}/getUpdates", params={"timeout": 30, "offset": offset}, timeout=35)
            data = r.json()
            if not data.get("ok"):
                time.sleep(2)
                continue

            for upd in data.get("result", []):
                offset = upd["update_id"] + 1

                msg = upd.get("message")
                if msg:
                    from_id = msg.get("from", {}).get("id")
                    if from_id == settings.owner_id:
                        _handle_owner_command(msg)
                    continue

                cq = upd.get("callback_query")
                if not cq:
                    continue

                from_id = cq["from"]["id"]
                callback_id = cq["id"]
                if from_id != settings.owner_id:
                    _answer_callback(callback_id, "Нет доступа")
                    continue

                msg = cq.get("message")
                if not msg:
                    _answer_callback(callback_id, "Нет сообщения")
                    continue

                chat_id = msg["chat"]["id"]
                message_id = msg["message_id"]
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
                    _edit_keyboard(chat_id, message_id, _build_toggled_keyboard(existing_keyboard, is_bl, user_id))
                    _answer_callback(callback_id, "Добавил в ЧС ✅")
                elif action == "off":
                    is_bl = _set_blacklist(user_id, False)
                    _edit_keyboard(chat_id, message_id, _build_toggled_keyboard(existing_keyboard, is_bl, user_id))
                    _answer_callback(callback_id, "Убрал из ЧС ✅")
                else:
                    _answer_callback(callback_id, "Неизвестное действие")

        except Exception as e:
            logger.error(f"[BOT] updates loop error: {e}")
            time.sleep(2)
