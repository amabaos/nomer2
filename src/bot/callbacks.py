import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import requests
from loguru import logger
from sqlalchemy.exc import IntegrityError

from src.accounts.repo import list_accounts
from src.assignments.repo import load_assignments
from src.core.config import settings
from src.db.database import SessionLocal
from src.db.models import Blacklist
from src.groups.repo import load_groups, set_chat_active
from src.stats_service import collect_system_stats

API = f"https://api.telegram.org/bot{settings.bot_token}"


def _post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        r = requests.post(f"{API}/{method}", json=payload, timeout=10)
        return r.json() if r.content else {"ok": r.ok}
    except Exception as e:
        logger.error(f"{method} error: {e}")
        return {"ok": False, "description": str(e)}


def _send_message(chat_id: int, text: str, reply_markup: Optional[dict] = None):
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _post("sendMessage", payload)


def _answer_callback(callback_query_id: str, text: str = ""):
    _post(
        "answerCallbackQuery",
        {"callback_query_id": callback_query_id, "text": text, "show_alert": False},
    )


def _edit_keyboard(chat_id: int, message_id: int, new_reply_markup: dict):
    _post(
        "editMessageReplyMarkup",
        {"chat_id": chat_id, "message_id": message_id, "reply_markup": new_reply_markup},
    )


def _edit_text(chat_id: int, message_id: int, text: str, reply_markup: Optional[dict] = None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _post("editMessageText", payload)


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


def _main_menu_keyboard() -> dict:
    return {
        "keyboard": [
            [{"text": "🔔 Включить ленту"}, {"text": "🔕 Выключить ленту"}],
            [{"text": "📁 Списки"}, {"text": "🛠 Админка"}],
            [{"text": "👤 Профиль"}, {"text": "📊 Статистика"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def _send_main_menu(chat_id: int):
    _send_message(
        chat_id,
        "👋 Панель администратора\nВыбери раздел кнопками ниже.",
        reply_markup=_main_menu_keyboard(),
    )


def _handle_profile(chat_id: int, from_user: Dict[str, Any]):
    first_name = from_user.get("first_name") or "-"
    username = from_user.get("username")
    username_text = f"@{username}" if username else "нет"

    _send_message(
        chat_id,
        "👤 Ваш статус:\n"
        f"ID: {from_user.get('id')}\n"
        f"Имя: {first_name}\n"
        f"Username: {username_text}\n"
        "Роль: owner\n"
        "Подписка: нет",
        reply_markup=_main_menu_keyboard(),
    )


def _format_stats_text() -> str:
    s = collect_system_stats()
    return (
        "📊 Статистика MVP:\n"
        f"Аккаунты: {s['accounts_total']} (active={s['accounts_active']}, paused={s['accounts_paused']}, disabled={s['accounts_disabled']}, errors={s['accounts_error']})\n"
        f"Мониторинг чатов: {s['chats_monitored']}\n"
        f"Найдено/отправлено лидов: {s['leads_sent_total']}\n"
        f"Blacklist: {s['blacklist_total']}\n"
        f"Join queue: total={s['join']['total']} queued={s['join']['queued']} in_progress={s['join']['in_progress']} failed={s['join']['failed']}"
    )


def _get_chats_index() -> Dict[int, Dict[str, Any]]:
    index: Dict[int, Dict[str, Any]] = OrderedDict()
    groups = load_groups()

    for g in groups:
        gname = g.get("name") or "-"
        for c in g.get("chats", []) or []:
            chat_id = c.get("id")
            if chat_id is None:
                continue
            chat_id = int(chat_id)

            if chat_id not in index:
                index[chat_id] = {
                    "id": chat_id,
                    "title": c.get("title") or c.get("username") or "Без названия",
                    "username": c.get("username"),
                    "active": bool(c.get("active", True)),
                    "added_at": c.get("added_at"),
                    "groups": [gname],
                }
            else:
                index[chat_id]["groups"].append(gname)
                if not index[chat_id].get("username") and c.get("username"):
                    index[chat_id]["username"] = c.get("username")
                if c.get("title") and index[chat_id]["title"] == "Без названия":
                    index[chat_id]["title"] = c.get("title")
                index[chat_id]["active"] = index[chat_id]["active"] or bool(c.get("active", True))
                if not index[chat_id].get("added_at") and c.get("added_at"):
                    index[chat_id]["added_at"] = c.get("added_at")

    return index


def _build_chats_keyboard() -> dict:
    index = _get_chats_index()
    rows: List[List[Dict[str, str]]] = []

    for chat_id, info in index.items():
        title = str(info.get("title") or "Без названия").strip()
        label = f"{title} ({chat_id})"
        if len(label) > 52:
            label = label[:49] + "..."

        active = bool(info.get("active", True))
        rows.append(
            [
                {"text": label, "callback_data": f"chat:open:{chat_id}"},
                {
                    "text": "❌" if active else "➕",
                    "callback_data": f"chat:toggle:{chat_id}:{'off' if active else 'on'}:list",
                },
            ]
        )

    rows.append([{"text": "🔄 Обновить", "callback_data": "chat:list"}])
    return {"inline_keyboard": rows}


def _render_chat_list_text() -> str:
    total = len(_get_chats_index())
    return f"📋 Список чатов\nВсего: {total}\n\nНажми на название, чтобы открыть карточку."


def _send_chat_list(chat_id: int):
    _send_message(chat_id, _render_chat_list_text(), reply_markup=_build_chats_keyboard())


def _format_chat_details(chat_id: int) -> str:
    info = _get_chats_index().get(chat_id)
    if not info:
        return f"Чат {chat_id} не найден"

    assignments = load_assignments()
    on_accounts: List[int] = []
    for acc_id, ids in assignments.items():
        if chat_id in {int(x) for x in (ids or [])}:
            on_accounts.append(int(acc_id))

    username = info.get("username")
    link = f"https://t.me/{username}" if username else "публичной ссылки нет"
    state = "активен" if info.get("active", True) else "отключён"
    added_at = info.get("added_at") or "неизвестно"
    groups = ", ".join(info.get("groups") or []) or "-"
    accounts_text = ", ".join(str(x) for x in sorted(on_accounts)) if on_accounts else "не назначен"

    return (
        "💬 Карточка чата\n"
        f"Название: {info.get('title')}\n"
        f"ID: {chat_id}\n"
        f"Состояние: {state}\n"
        f"Ссылка: {link}\n"
        f"Дата добавления: {added_at}\n"
        f"Группы: {groups}\n"
        f"Аккаунты: {accounts_text}"
    )


def _build_chat_details_keyboard(chat_id: int) -> dict:
    info = _get_chats_index().get(chat_id)
    active = bool(info.get("active", True)) if info else True

    return {
        "inline_keyboard": [
            [
                {
                    "text": "❌ Удалить" if active else "➕ Вернуть",
                    "callback_data": f"chat:toggle:{chat_id}:{'off' if active else 'on'}:details",
                }
            ],
            [{"text": "⬅️ К списку", "callback_data": "chat:list"}],
        ]
    }


def _show_lists_menu(chat_id: int):
    _send_message(
        chat_id,
        "📁 Раздел «Списки»",
        reply_markup={
            "inline_keyboard": [
                [{"text": "💬 Список чатов", "callback_data": "chat:list"}],
                [{"text": "🧩 Список групп", "callback_data": "groups:list"}],
            ]
        },
    )


def _handle_owner_command(msg: dict) -> bool:
    text = (msg.get("text") or "").strip()
    chat_id = msg["chat"]["id"]
    from_user = msg.get("from", {})

    if text in {"/start", "/menu", "/help"}:
        _send_main_menu(chat_id)
        return True

    if text in {"/ping", "ping"}:
        _send_message(chat_id, "pong ✅", reply_markup=_main_menu_keyboard())
        return True

    if text in {"/accounts", "🛠 Админка"}:
        accs = list_accounts()
        if not accs:
            _send_message(chat_id, "Аккаунтов пока нет", reply_markup=_main_menu_keyboard())
            return True
        rows = [f"#{a.id} | {a.phone} | status={a.status}" for a in accs]
        _send_message(chat_id, "🛠 Аккаунты:\n" + "\n".join(rows), reply_markup=_main_menu_keyboard())
        return True

    if text in {"/groups"}:
        groups = load_groups()
        if not groups:
            _send_message(chat_id, "Групп пока нет", reply_markup=_main_menu_keyboard())
            return True
        rows = [f"{g['name']} | enabled={g.get('enabled', True)} | keywords={len(g.get('keywords', []))} | chats={len(g.get('chats', []))}" for g in groups]
        _send_message(chat_id, "Группы:\n" + "\n".join(rows), reply_markup=_main_menu_keyboard())
        return True

    if text in {"/stats", "📊 Статистика"}:
        _send_message(chat_id, _format_stats_text(), reply_markup=_main_menu_keyboard())
        return True

    if text == "👤 Профиль":
        _handle_profile(chat_id, from_user)
        return True

    if text == "📁 Списки":
        _show_lists_menu(chat_id)
        return True

    if text == "🔔 Включить ленту":
        _send_message(chat_id, "Лента уже активна ✅", reply_markup=_main_menu_keyboard())
        return True

    if text == "🔕 Выключить ленту":
        _send_message(chat_id, "Отключение ленты пока в разработке.", reply_markup=_main_menu_keyboard())
        return True

    return False


def _handle_chat_callback(callback_id: str, chat_id: int, message_id: int, data_str: str):
    parts = data_str.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "list":
        _edit_text(chat_id, message_id, _render_chat_list_text(), reply_markup=_build_chats_keyboard())
        _answer_callback(callback_id)
        return

    if action == "open" and len(parts) >= 3:
        cid = int(parts[2])
        _edit_text(chat_id, message_id, _format_chat_details(cid), reply_markup=_build_chat_details_keyboard(cid))
        _answer_callback(callback_id)
        return

    if action == "toggle" and len(parts) >= 5:
        cid = int(parts[2])
        target_state = parts[3]
        view = parts[4]
        active = target_state == "on"
        set_chat_active(cid, active)

        if view == "details":
            _edit_text(chat_id, message_id, _format_chat_details(cid), reply_markup=_build_chat_details_keyboard(cid))
        else:
            _edit_text(chat_id, message_id, _render_chat_list_text(), reply_markup=_build_chats_keyboard())

        _answer_callback(callback_id, "Готово ✅")
        return

    _answer_callback(callback_id, "Неизвестная кнопка")


def _handle_groups_callback(callback_id: str, chat_id: int, message_id: int, data_str: str):
    if data_str != "groups:list":
        _answer_callback(callback_id, "Неизвестная кнопка")
        return

    groups = load_groups()
    if not groups:
        _edit_text(chat_id, message_id, "🧩 Групп пока нет")
        _answer_callback(callback_id)
        return

    lines = ["🧩 Список групп:"]
    for g in groups:
        lines.append(
            f"• {g.get('name')} | enabled={g.get('enabled', True)} | keywords={len(g.get('keywords', []))} | chats={len(g.get('chats', []))}"
        )
    _edit_text(chat_id, message_id, "\n".join(lines))
    _answer_callback(callback_id)


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

                if data_str.startswith("bl:"):
                    parts = data_str.split(":")
                    if len(parts) != 3:
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
                    continue

                if data_str.startswith("chat:"):
                    _handle_chat_callback(callback_id, chat_id, message_id, data_str)
                    continue

                if data_str.startswith("groups:"):
                    _handle_groups_callback(callback_id, chat_id, message_id, data_str)
                    continue

                _answer_callback(callback_id, "Неизвестная кнопка")

        except Exception as e:
            logger.error(f"[BOT] updates loop error: {e}")
            time.sleep(2)
