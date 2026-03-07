import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import html
import os
import re
import tempfile
import uuid

import requests
from loguru import logger
from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError

from src.accounts.models import Account
from src.accounts.repo import delete_account, get_next_id, list_accounts, upsert_account
from src.assignments.repo import load_assignments, save_assignments
from src.assignments.repo import auto_assign_chat_ids_round_robin
from src.core.config import settings
from src.db.database import SessionLocal
from src.db.models import Blacklist, LeadEvent
from src.gateways.telegram_client import make_client
from src.groups.repo import (
    add_keyword,
    create_group,
    get_group,
    load_groups,
    remove_chat_everywhere,
    remove_keyword,
    set_chat_active,
    set_keyword_active,
)
from src.join.models import JoinTask
from src.join.repo import add_tasks
from src.runtime_settings import load_runtime_settings, save_runtime_settings
from src.stats_service import collect_system_stats

API = f"https://api.telegram.org/bot{settings.bot_token}"
_STATE: Dict[int, Dict[str, Any]] = {}


def _post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        r = requests.post(f"{API}/{method}", json=payload, timeout=12)
        return r.json() if r.content else {"ok": r.ok}
    except Exception as e:
        logger.error(f"{method} error: {e}")
        return {"ok": False, "description": str(e)}


def _send_message(chat_id: int, text: str, reply_markup: Optional[dict] = None, parse_mode: Optional[str] = None):
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
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


def _edit_text(
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: Optional[str] = None,
):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    _post("editMessageText", payload)


def _send_document(chat_id: int, file_path: str, caption: str = ""):
    try:
        with open(file_path, "rb") as f:
            files = {"document": f}
            data = {"chat_id": chat_id, "caption": caption}
            requests.post(f"{API}/sendDocument", data=data, files=files, timeout=30)
    except Exception as e:
        logger.error(f"sendDocument error: {e}")


def _normalize_chat_handle(raw: str) -> Optional[str]:
    s = (raw or "").strip().strip('"').strip("'")
    if not s:
        return None
    if s.startswith("http://t.me/"):
        s = "https://" + s[len("http://"):]
    if s.startswith("https://t.me/"):
        return s
    if s.startswith("t.me/"):
        return "https://" + s
    if s.startswith("@"):
        return s
    if re.fullmatch(r"[A-Za-z0-9_]+", s):
        return f"@{s}"
    return None


def _parse_chat_lines(text: str) -> List[str]:
    uniq: List[str] = []
    seen = set()
    for line in (text or "").splitlines():
        h = _normalize_chat_handle(line)
        if not h:
            continue
        k = h.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(h)
    return uniq


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

    toggle_row = (
        [{"text": "✅ Разблокировать", "callback_data": f"bl:off:{user_id}"}]
        if is_blacklisted
        else [{"text": "🚫 В ЧС", "callback_data": f"bl:on:{user_id}"}]
    )
    kept_rows.append(toggle_row)
    return {"inline_keyboard": kept_rows}


def _main_menu_keyboard() -> dict:
    return {
        "keyboard": [
            [{"text": "🏠 Главная"}],
            [{"text": "👤 Аккаунты"}, {"text": "💬 Чаты"}],
            [{"text": "🔑 Ключевые слова"}, {"text": "📥 Результаты"}],
            [{"text": "📊 Статистика"}, {"text": "⚙️ Настройки"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def _home_inline_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "👤 Аккаунты", "callback_data": "acc:list"},
                {"text": "💬 Чаты", "callback_data": "chat:list"},
            ],
            [
                {"text": "🔑 Ключевые слова", "callback_data": "kw:list"},
                {"text": "📥 Результаты", "callback_data": "res:list"},
            ],
            [
                {"text": "📊 Статистика", "callback_data": "stats:all"},
            ],
        ]
    }


def _fmt_status(st: str) -> str:
    m = {
        "active": "✅ Активен",
        "paused": "⏸ На паузе",
        "disabled": "🚫 Отключён",
        "error": "⚠️ Ошибка",
        "authorization_required": "🔒 Нужна авторизация",
        "inactive": "🟡 Не запущен",
    }
    return m.get(st or "inactive", "🟡 Неизвестно")


def _home_text() -> str:
    s = collect_system_stats()
    parser_on = "✅ Включён" if s.get("accounts_active", 0) > 0 else "⏸ На паузе"
    return (
        "🏠 HR Prime версия 1.0 — панель управления\n\n"
        f"👤 Аккаунтов: {s['accounts_total']} (активных: {s['accounts_active']})\n"
        f"💬 Чатов в работе: {s['chats_active']} из {s['chats_total']}\n"
        f"🔑 Ключей активно: {s['keywords_active']} из {s['keywords_total']}\n"
        f"⚙️ Парсинг: {parser_on}\n"
        f"📥 Совпадений: {s['leads_24h']} за 24ч / {s['leads_7d']} за 7д"
    )


def _send_home(chat_id: int):
    _send_message(chat_id, _home_text(), reply_markup=_main_menu_keyboard())


def _render_accounts_text() -> str:
    accs = list_accounts()
    if not accs:
        return "👤 Аккаунты\n\nПока нет подключённых аккаунтов."

    lines = ["👤 Аккаунты", ""]
    for a in accs:
        title = (a.title or a.stage or f"Аккаунт {a.id}")[:32]
        lines.append(f"• {title} (ID: {a.id}) {a.phone} — {_fmt_status(a.status)}")
    return "\n".join(lines)


def _accounts_keyboard() -> dict:
    rows: List[List[Dict[str, str]]] = []
    for a in list_accounts():
        action = "pause" if a.status == "active" else "start"
        action_text = "⏸" if a.status == "active" else "▶️"
        rows.append(
            [
                {"text": f"#{a.id}", "callback_data": f"acc:open:{a.id}"},
                {"text": action_text, "callback_data": f"acc:toggle:{a.id}:{action}"},
                {"text": "🗑", "callback_data": f"acc:del:{a.id}"},
            ]
        )
    rows.append([{"text": "➕ Добавить аккаунт", "callback_data": "acc:add:start"}])
    rows.append(
        [
            {"text": "🔄 Обновить", "callback_data": "acc:list"},
            {"text": "🏠 На главную", "callback_data": "home"},
        ]
    )
    return {"inline_keyboard": rows}


def _render_account_card(account_id: int) -> str:
    acc = next((a for a in list_accounts() if a.id == int(account_id)), None)
    if not acc:
        return "Аккаунт не найден"

    assignments = load_assignments().get(str(acc.id), [])
    title = (acc.title or acc.stage or f"Аккаунт {acc.id}")[:32]
    return (
        f"👤 {title} (ID: {acc.id})\n"
        f"📱 Номер: {acc.phone}\n"
        f"🙍 Username: @{acc.username if acc.username else '-'}\n"
        f"🧾 Статус: {_fmt_status(acc.status)}\n"
        f"💬 Назначено чатов: {len(assignments)}\n"
        f"🌐 Прокси: {'есть' if acc.proxy else 'нет'}"
    )


def _account_card_keyboard(account_id: int) -> dict:
    acc = next((a for a in list_accounts() if a.id == int(account_id)), None)
    if not acc:
        return {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "acc:list"}]]}

    action = "pause" if acc.status == "active" else "start"
    action_text = "⏸ Пауза" if acc.status == "active" else "▶️ Запустить"
    return {
        "inline_keyboard": [
            [{"text": "✏️ Название", "callback_data": f"acc:rename:{acc.id}"}],
            [{"text": "➕ Добавить чат", "callback_data": f"acc:addchats:{acc.id}"}],
            [{"text": "📤 Выгрузить список чатов", "callback_data": f"acc:export:{acc.id}"}],
            [{"text": "🌐 Изменить прокси", "callback_data": f"acc:proxy:{acc.id}"}],
            [{"text": action_text, "callback_data": f"acc:toggle:{acc.id}:{action}"}],
            [{"text": "🗑 Удалить аккаунт", "callback_data": f"acc:del:{acc.id}"}],
            [
                {"text": "⬅️ К списку", "callback_data": "acc:list"},
                {"text": "🏠 На главную", "callback_data": "home"},
            ],
        ]
    }


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


def _render_chat_list_text() -> str:
    total = len(_get_chats_index())
    return f"💬 Чаты\n\nВсего чатов: {total}\nНажми на чат, чтобы открыть карточку."


def _build_chats_keyboard() -> dict:
    index = _get_chats_index()
    rows: List[List[Dict[str, str]]] = []

    for chat_id, info in index.items():
        title = str(info.get("title") or "Без названия").strip()
        label = f"{title} ({chat_id})"
        if len(label) > 50:
            label = label[:47] + "..."

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

    rows.append(
        [{"text": "➕ Добавить чаты", "callback_data": "chat:addbulk"}]
    )
    rows.append(
        [
            {"text": "🔄 Обновить", "callback_data": "chat:list"},
            {"text": "🏠 На главную", "callback_data": "home"},
        ]
    )
    return {"inline_keyboard": rows}


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
    state = "✅ Активен" if info.get("active", True) else "⏸ Отключён"
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
                    "text": "❌ Отключить" if active else "➕ Включить",
                    "callback_data": f"chat:toggle:{chat_id}:{'off' if active else 'on'}:details",
                }
            ],
            [{"text": "🗑 Удалить из бота", "callback_data": f"chat:remove:{chat_id}"}],
            [
                {"text": "⬅️ К списку", "callback_data": "chat:list"},
                {"text": "🏠 На главную", "callback_data": "home"},
            ],
        ]
    }


def _groups_brief_index() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = OrderedDict()
    for g in load_groups():
        kws = g.get("keywords") or []
        total = len(kws)
        active = sum(1 for kw in kws if kw.get("active", True))
        out[g.get("name")] = {
            "enabled": bool(g.get("enabled", True)),
            "keywords_total": total,
            "keywords_active": active,
        }
    return out


def _keywords_groups_text() -> str:
    groups = _groups_brief_index()
    if not groups:
        return "🔑 Ключевые слова\n\nНет групп с ключевыми словами."

    lines = ["🔑 Ключевые слова", ""]
    for name, info in groups.items():
        state = "✅" if info["enabled"] else "⏸"
        lines.append(f"• {state} {name}: {info['keywords_active']} из {info['keywords_total']}")
    return "\n".join(lines)


def _keywords_groups_keyboard() -> dict:
    rows = [
        [{"text": f"📂 {name}", "callback_data": f"kw:g:{name}"}]
        for name in _groups_brief_index().keys()
    ]
    rows.append([{"text": "➕ Новая группа", "callback_data": "kw:newgroup"}])
    rows.append(
        [
            {"text": "🔄 Обновить", "callback_data": "kw:list"},
            {"text": "🏠 На главную", "callback_data": "home"},
        ]
    )
    return {"inline_keyboard": rows}


def _group_keywords_text(group_name: str) -> str:
    g = get_group(group_name)
    if not g:
        return "Группа не найдена"

    lines = [f"🔑 Группа: {group_name}", ""]
    kws = g.get("keywords") or []
    if not kws:
        lines.append("Ключевых слов пока нет.")
    else:
        for item in kws:
            st = "✅" if item.get("active", True) else "⏸"
            lines.append(f"• {st} {item.get('text')}")
    return "\n".join(lines)


def _group_keywords_keyboard(group_name: str) -> dict:
    g = get_group(group_name)
    rows: List[List[Dict[str, str]]] = []
    if g:
        for item in (g.get("keywords") or []):
            text = item.get("text")
            active = bool(item.get("active", True))
            rows.append(
                [
                    {"text": f"{text}", "callback_data": "noop"},
                    {
                        "text": "⏸" if active else "▶️",
                        "callback_data": f"kw:toggle:{group_name}:{text}:{'off' if active else 'on'}",
                    },
                    {"text": "🗑", "callback_data": f"kw:del:{group_name}:{text}"},
                ]
            )
    rows.append([{"text": "➕ Добавить ключ", "callback_data": f"kw:add:{group_name}"}])
    rows.append(
        [
            {"text": "⬅️ К группам", "callback_data": "kw:list"},
            {"text": "🏠 На главную", "callback_data": "home"},
        ]
    )
    return {"inline_keyboard": rows}


def _build_chat_link(chat_title: str, chat_username: Optional[str]) -> str:
    safe_title = html.escape(chat_title or "Чат")
    if chat_username:
        return f'<a href="https://t.me/{chat_username}">{safe_title}</a>'
    return safe_title


def _build_message_link(chat_username: Optional[str], message_id: Optional[int]) -> str:
    if chat_username and message_id:
        return f'<a href="https://t.me/{chat_username}/{int(message_id)}">Открыть сообщение</a>'
    return "—"


def _recent_results_text(limit: int = 10) -> str:
    db = SessionLocal()
    try:
        rows = db.query(LeadEvent).order_by(desc(LeadEvent.id)).limit(limit).all()
    finally:
        db.close()

    if not rows:
        return "📥 Результаты\n\nПока нет сохранённых совпадений."

    lines = ["📥 Последние совпадения", ""]
    for x in rows:
        when = x.created_at.isoformat(sep=" ", timespec="minutes") if x.created_at else "-"
        author = (
            f"@{x.author_username}"
            if x.author_username
            else (str(x.author_id) if x.author_id else "неизвестно")
        )
        lines.append(f"• {when}")
        lines.append(f"Чат: {_build_chat_link(x.chat_title or str(x.chat_id), x.chat_username)}")
        lines.append(f"Ключ: {html.escape(x.keyword or '-')} | Автор: {html.escape(author)}")
        lines.append(f"Текст сообщения: {_build_message_link(x.chat_username, getattr(x, 'message_id', None))}")
        lines.append("")
    return "\n".join(lines)


def _render_today_results_file() -> str:
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    db = SessionLocal()
    try:
        rows = (
            db.query(LeadEvent)
            .filter(LeadEvent.created_at >= start, LeadEvent.created_at < end)
            .order_by(LeadEvent.id.asc())
            .all()
        )
    finally:
        db.close()

    fd, path = tempfile.mkstemp(prefix="results_today_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for x in rows:
            when = x.created_at.isoformat(sep=" ", timespec="minutes") if x.created_at else "-"
            chat_link = f"https://t.me/{x.chat_username}" if x.chat_username else "-"
            msg_link = (
                f"https://t.me/{x.chat_username}/{int(x.message_id)}"
                if x.chat_username and getattr(x, "message_id", None)
                else "-"
            )
            f.write(f"{when} | {x.chat_title or x.chat_id} | chat={chat_link} | msg={msg_link}\n")
    return path


def send_daily_results_report() -> None:
    report = _render_today_results_file()
    _send_document(settings.owner_id, report, "📥 Авто-выгрузка результатов за сегодня")


def _results_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "📤 Выгрузить за сегодня", "callback_data": "res:export:today"}],
            [
                {"text": "🔄 Обновить", "callback_data": "res:list"},
                {"text": "🏠 На главную", "callback_data": "home"},
            ]
        ]
    }


def _stats_text() -> str:
    s = collect_system_stats()
    return (
        "📊 Общая статистика\n\n"
        f"👤 Аккаунтов: {s['accounts_total']} (активных: {s['accounts_active']}, ошибок: {s['accounts_error']})\n"
        f"💬 Чатов: {s['chats_total']} (в работе: {s['chats_active']})\n"
        f"🔑 Ключей: {s['keywords_total']} (активных: {s['keywords_active']})\n"
        f"📥 Совпадений: {s['leads_1h']} за 1ч / {s['leads_24h']} за 24ч / {s['leads_7d']} за 7д\n"
        f"🚫 В чёрном списке: {s['blacklist_total']}"
    )


def _stats_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🔄 Обновить", "callback_data": "stats:all"},
                {"text": "🏠 На главную", "callback_data": "home"},
            ]
        ]
    }


def _settings_text() -> str:
    cfg = load_runtime_settings()
    return (
        "⚙️ Настройки\n\n"
        "Парсинг работает 24/7.\n"
        f"• Lead TTL (часы): {cfg['lead_ttl_hours']}\n"
        f"• Интервал вступления в чаты (сек): {cfg['join_interval_seconds']}"
    )


def _settings_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🕒 Изменить Lead TTL (часы)", "callback_data": "settings:leadttl"}],
            [{"text": "⏱ Изменить интервал Join (сек)", "callback_data": "settings:joininterval"}],
            [{"text": "🏠 На главную", "callback_data": "home"}],
        ]
    }


def _set_state(owner_id: int, action: str, **payload):
    _STATE[int(owner_id)] = {"action": action, **payload}


def _clear_state(owner_id: int):
    _STATE.pop(int(owner_id), None)


def _toggle_parser_all(start: bool) -> str:
    accs = list_accounts()
    changed = 0
    for acc in accs:
        if start:
            if acc.status in {"paused", "inactive"}:
                acc.status = "active"
                acc.status_reason = None
                upsert_account(acc)
                changed += 1
        else:
            if acc.status == "active":
                acc.status = "paused"
                acc.status_reason = "Пауза через UI"
                upsert_account(acc)
                changed += 1

    if start:
        return f"▶️ Запуск применён. Аккаунтов переведено в активный режим: {changed}."
    return f"⏸ Пауза применена. Аккаунтов поставлено на паузу: {changed}."


def _handle_state_input(msg: dict) -> bool:
    owner_id = int(msg.get("from", {}).get("id") or 0)
    state = _STATE.get(owner_id)
    if not state:
        return False

    text = (msg.get("text") or "").strip()
    chat_id = msg["chat"]["id"]

    if text == "Отмена":
        _clear_state(owner_id)
        _send_message(chat_id, "Действие отменено.", reply_markup=_main_menu_keyboard())
        return True

    action = state.get("action")
    try:
        if action == "acc_add_phone":
            _set_state(owner_id, "acc_add_session_path", phone=text)
            _send_message(chat_id, "Шаг 2/4. Отправь путь к session-файлу.")
            return True

        if action == "acc_add_session_path":
            if not Path(text).exists():
                _send_message(chat_id, "Файл сессии не найден. Отправь корректный путь.")
                return True
            payload = dict(state)
            payload["session_path"] = text
            _set_state(owner_id, "acc_add_password", **payload)
            _send_message(chat_id, "Шаг 3/4. Если есть пароль 2FA — отправь его. Если нет, отправь '-'.")
            return True

        if action == "acc_add_password":
            payload = dict(state)
            payload["password"] = None if text == "-" else text
            _set_state(owner_id, "acc_add_proxy", **payload)
            _send_message(chat_id, "Шаг 4/4. Отправь прокси (socks5://...), либо нажми 'Пропустить прокси'.")
            return True

        if action == "acc_add_proxy":
            proxy = None if text.lower() in {"пропустить", "-"} else text
            phone = state.get("phone")
            session_path = state.get("session_path")
            new_id = get_next_id(list_accounts())
            acc = Account(
                id=new_id,
                phone=phone,
                stage=f"Account {new_id}",
                title=f"Account {new_id}",
                session_path=session_path,
                proxy=proxy,
                status="inactive",
            )

            async def _check():
                async with make_client(session_path, proxy) as app:
                    me = await app.get_me()
                    return me

            import asyncio

            me = asyncio.run(_check())
            acc.status = "active"
            acc.username = getattr(me, "username", None)
            acc.name = (f"{getattr(me, 'first_name', '')} {getattr(me, 'last_name', '')}".strip() or None)
            upsert_account(acc)
            _clear_state(owner_id)
            _send_message(chat_id, f"✅ Аккаунт #{acc.id} подключён.", reply_markup=_main_menu_keyboard())
            return True

        if action == "acc_rename":
            aid = int(state.get("account_id"))
            acc = next((a for a in list_accounts() if a.id == aid), None)
            if not acc:
                _send_message(chat_id, "Аккаунт не найден")
                _clear_state(owner_id)
                return True
            acc.title = text[:32]
            upsert_account(acc)
            _clear_state(owner_id)
            _send_message(chat_id, "✅ Название обновлено.")
            return True

        if action == "acc_set_proxy":
            aid = int(state.get("account_id"))
            acc = next((a for a in list_accounts() if a.id == aid), None)
            if not acc:
                _send_message(chat_id, "Аккаунт не найден")
            else:
                acc.proxy = None if text.lower() in {"удалить", "-", "пропустить"} else text
                upsert_account(acc)
                _send_message(chat_id, "✅ Прокси обновлен.")
            _clear_state(owner_id)
            return True

        if action in {"chat_add_bulk", "acc_add_chats"}:
            handles = _parse_chat_lines(text)
            if not handles:
                _send_message(chat_id, "Не нашел валидных чатов. Отправь список по строкам.")
                return True

            accs = [a for a in list_accounts() if a.status in {"active", "inactive", "paused"}]
            if action == "acc_add_chats":
                aid = int(state.get("account_id"))
                accs = [a for a in accs if a.id == aid]
            if not accs:
                _send_message(chat_id, "Нет доступных аккаунтов для назначения.")
                return True

            dist = auto_assign_chat_ids_round_robin([a.id for a in accs], list(range(1, len(handles) + 1)))
            tasks: List[JoinTask] = []
            for acc in accs:
                for idx in dist.get(acc.id, []):
                    handle = handles[idx - 1]
                    tasks.append(JoinTask(id=str(uuid.uuid4()), group="General", handle=handle, assigned_account_id=acc.id))
            added = add_tasks(tasks)
            _clear_state(owner_id)
            _send_message(chat_id, f"✅ Принято чатов: {len(handles)}. В очередь добавлено: {added}.")
            return True

        if action == "kw_add":
            group = state.get("group")
            add_keyword(group, text)
            _clear_state(owner_id)
            _send_message(
                chat_id,
                f"✅ Ключ добавлен в группу {group}.",
                reply_markup=_main_menu_keyboard(),
            )
            return True

        if action == "kw_new_group":
            create_group(text)
            _clear_state(owner_id)
            _send_message(chat_id, f"✅ Группа '{text}' создана.")
            return True

        if action == "set_lead_ttl":
            cfg = save_runtime_settings({"lead_ttl_hours": int(text)})
            _clear_state(owner_id)
            _send_message(chat_id, f"✅ Lead TTL обновлен: {cfg['lead_ttl_hours']} ч.")
            return True

        if action == "set_join_interval":
            cfg = save_runtime_settings({"join_interval_seconds": int(text)})
            _clear_state(owner_id)
            _send_message(chat_id, f"✅ Интервал Join обновлен: {cfg['join_interval_seconds']} сек.")
            return True

    except Exception as e:
        _send_message(chat_id, f"⚠️ Ошибка: {e}", reply_markup=_main_menu_keyboard())
        _clear_state(owner_id)
        return True

    _clear_state(owner_id)
    return False


def _handle_owner_command(msg: dict) -> bool:
    if _handle_state_input(msg):
        return True

    text = (msg.get("text") or "").strip()
    chat_id = msg["chat"]["id"]
    _ = msg.get("from", {})

    if text in {"/start", "/menu", "/help", "🏠 Главная"}:
        _send_home(chat_id)
        return True

    if text in {"/ping", "ping"}:
        _send_message(chat_id, "pong ✅", reply_markup=_main_menu_keyboard())
        return True

    if text == "👤 Аккаунты":
        _send_message(chat_id, _render_accounts_text(), reply_markup=_accounts_keyboard())
        return True

    if text == "💬 Чаты":
        _send_message(chat_id, _render_chat_list_text(), reply_markup=_build_chats_keyboard())
        return True

    if text == "🔑 Ключевые слова":
        _send_message(chat_id, _keywords_groups_text(), reply_markup=_keywords_groups_keyboard())
        return True

    if text == "📥 Результаты":
        _send_message(chat_id, _recent_results_text(), reply_markup=_results_keyboard(), parse_mode="HTML")
        return True

    if text in {"📊 Статистика", "/stats"}:
        _send_message(chat_id, _stats_text(), reply_markup=_stats_keyboard())
        return True

    if text == "⚙️ Настройки":
        _send_message(chat_id, _settings_text(), reply_markup=_settings_keyboard())
        return True

    return False


def _handle_acc_callback(callback_id: str, chat_id: int, message_id: int, data_str: str):
    parts = data_str.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "list":
        _edit_text(chat_id, message_id, _render_accounts_text(), reply_markup=_accounts_keyboard())
        _answer_callback(callback_id)
        return

    if action == "open" and len(parts) >= 3:
        aid = int(parts[2])
        _edit_text(
            chat_id,
            message_id,
            _render_account_card(aid),
            reply_markup=_account_card_keyboard(aid),
        )
        _answer_callback(callback_id)
        return

    if action == "toggle" and len(parts) >= 4:
        aid = int(parts[2])
        mode = parts[3]
        acc = next((a for a in list_accounts() if a.id == aid), None)
        if not acc:
            _answer_callback(callback_id, "Аккаунт не найден")
            return

        acc.status = "paused" if mode == "pause" else "active"
        acc.status_reason = "Пауза через UI" if mode == "pause" else None
        upsert_account(acc)

        _edit_text(
            chat_id,
            message_id,
            _render_account_card(aid),
            reply_markup=_account_card_keyboard(aid),
        )
        _answer_callback(callback_id, "Готово ✅")
        return

    if action == "del" and len(parts) >= 3:
        aid = int(parts[2])
        ok = delete_account(aid)
        if ok:
            assigns = load_assignments()
            assigns.pop(str(aid), None)
            save_assignments(assigns)

        _edit_text(chat_id, message_id, _render_accounts_text(), reply_markup=_accounts_keyboard())
        _answer_callback(callback_id, "Удалено ✅" if ok else "Не найдено")
        return

    if action == "add" and len(parts) >= 3 and parts[2] == "start":
        _set_state(settings.owner_id, "acc_add_phone")
        _send_message(
            chat_id,
            "Шаг 1/4. Отправь номер телефона аккаунта в формате +380...\nДля отмены: Отмена",
            reply_markup={"keyboard": [[{"text": "Пропустить прокси"}], [{"text": "Отмена"}]], "resize_keyboard": True},
        )
        _answer_callback(callback_id, "Жду номер")
        return

    if action == "rename" and len(parts) >= 3:
        aid = int(parts[2])
        _set_state(settings.owner_id, "acc_rename", account_id=aid)
        _send_message(chat_id, "Введи новое название аккаунта (до 32 символов).")
        _answer_callback(callback_id, "Жду название")
        return

    if action == "proxy" and len(parts) >= 3:
        aid = int(parts[2])
        _set_state(settings.owner_id, "acc_set_proxy", account_id=aid)
        _send_message(chat_id, "Отправь новый прокси или 'удалить' чтобы убрать.")
        _answer_callback(callback_id, "Жду прокси")
        return

    if action == "addchats" and len(parts) >= 3:
        aid = int(parts[2])
        _set_state(settings.owner_id, "acc_add_chats", account_id=aid)
        _send_message(chat_id, "Отправь список чатов по строкам для этого аккаунта.")
        _answer_callback(callback_id, "Жду список")
        return

    if action == "export" and len(parts) >= 3:
        aid = int(parts[2])
        assignments = load_assignments().get(str(aid), [])
        index = _get_chats_index()
        fd, p = tempfile.mkstemp(prefix=f"account_{aid}_chats_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for cid in assignments:
                info = index.get(int(cid), {})
                title = info.get("title") or str(cid)
                username = info.get("username")
                link = f"https://t.me/{username}" if username else str(cid)
                f.write(f"{title} | {link}\n")
        _send_document(chat_id, p, f"Чаты аккаунта #{aid}")
        _answer_callback(callback_id, "Выгружено ✅")
        return

    if action == "add" and len(parts) >= 3 and parts[2] == "session":
        _set_state(settings.owner_id, "acc_add_phone")
        _send_message(
            chat_id,
            "Шаг 1/4. Отправь номер телефона аккаунта в формате +380...",
            reply_markup=_main_menu_keyboard(),
        )
        _answer_callback(callback_id, "Жду номер")
        return

    _answer_callback(callback_id, "Неизвестная кнопка")


def _handle_chat_callback(callback_id: str, chat_id: int, message_id: int, data_str: str):
    parts = data_str.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "list":
        _edit_text(chat_id, message_id, _render_chat_list_text(), reply_markup=_build_chats_keyboard())
        _answer_callback(callback_id)
        return

    if action == "addbulk":
        _set_state(settings.owner_id, "chat_add_bulk")
        _send_message(chat_id, "Отправь список чатов для массового вступления (по строкам).")
        _answer_callback(callback_id, "Жду список")
        return

    if action == "open" and len(parts) >= 3:
        cid = int(parts[2])
        _edit_text(
            chat_id,
            message_id,
            _format_chat_details(cid),
            reply_markup=_build_chat_details_keyboard(cid),
        )
        _answer_callback(callback_id)
        return

    if action == "toggle" and len(parts) >= 5:
        cid = int(parts[2])
        target_state = parts[3]
        view = parts[4]
        active = target_state == "on"
        set_chat_active(cid, active)

        if view == "details":
            _edit_text(
                chat_id,
                message_id,
                _format_chat_details(cid),
                reply_markup=_build_chat_details_keyboard(cid),
            )
        else:
            _edit_text(
                chat_id,
                message_id,
                _render_chat_list_text(),
                reply_markup=_build_chats_keyboard(),
            )

        _answer_callback(callback_id, "Готово ✅")
        return

    if action == "remove" and len(parts) >= 3:
        cid = int(parts[2])
        removed = remove_chat_everywhere(cid)

        assigns = load_assignments()
        for aid in list(assigns.keys()):
            assigns[aid] = [x for x in assigns.get(aid, []) if int(x) != cid]
        save_assignments(assigns)

        _edit_text(chat_id, message_id, _render_chat_list_text(), reply_markup=_build_chats_keyboard())
        _answer_callback(callback_id, f"Удалено: {removed} запис.")
        return

    _answer_callback(callback_id, "Неизвестная кнопка")


def _handle_keywords_callback(callback_id: str, chat_id: int, message_id: int, data_str: str):
    if data_str == "kw:newgroup":
        _set_state(settings.owner_id, "kw_new_group")
        _send_message(chat_id, "Введи название новой группы ключевых слов.")
        _answer_callback(callback_id, "Жду название")
        return

    if data_str == "kw:list":
        _edit_text(chat_id, message_id, _keywords_groups_text(), reply_markup=_keywords_groups_keyboard())
        _answer_callback(callback_id)
        return

    parts = data_str.split(":")
    if len(parts) < 3:
        _answer_callback(callback_id, "Неизвестная кнопка")
        return

    action = parts[1]
    group = parts[2]

    if action == "g":
        _edit_text(
            chat_id,
            message_id,
            _group_keywords_text(group),
            reply_markup=_group_keywords_keyboard(group),
        )
        _answer_callback(callback_id)
        return

    if action == "add":
        _set_state(settings.owner_id, "kw_add", group=group)
        _send_message(
            chat_id,
            f"Введи новое ключевое слово для группы {group}.\nДля отмены отправь: Отмена",
            reply_markup=_main_menu_keyboard(),
        )
        _answer_callback(callback_id, "Жду слово")
        return

    if action == "del" and len(parts) >= 4:
        phrase = parts[3]
        remove_keyword(group, phrase)
        _edit_text(
            chat_id,
            message_id,
            _group_keywords_text(group),
            reply_markup=_group_keywords_keyboard(group),
        )
        _answer_callback(callback_id, "Удалено ✅")
        return

    if action == "toggle" and len(parts) >= 5:
        phrase = parts[3]
        mode = parts[4]
        set_keyword_active(group, phrase, mode == "on")
        _edit_text(
            chat_id,
            message_id,
            _group_keywords_text(group),
            reply_markup=_group_keywords_keyboard(group),
        )
        _answer_callback(callback_id, "Готово ✅")
        return

    _answer_callback(callback_id, "Неизвестная кнопка")


def _handle_results_callback(callback_id: str, chat_id: int, message_id: int, data_str: str):
    if data_str == "res:export:today":
        report = _render_today_results_file()
        _send_document(chat_id, report, "Результаты за сегодня")
        _answer_callback(callback_id, "Готово ✅")
        return

    if data_str != "res:list":
        _answer_callback(callback_id, "Неизвестная кнопка")
        return

    _edit_text(chat_id, message_id, _recent_results_text(), reply_markup=_results_keyboard(), parse_mode="HTML")
    _answer_callback(callback_id)


def _handle_stats_callback(callback_id: str, chat_id: int, message_id: int, data_str: str):
    if data_str != "stats:all":
        _answer_callback(callback_id, "Неизвестная кнопка")
        return

    _edit_text(chat_id, message_id, _stats_text(), reply_markup=_stats_keyboard())
    _answer_callback(callback_id)


def _handle_parser_callback(callback_id: str, chat_id: int, message_id: int, data_str: str):
    if data_str == "parser:start":
        text = _toggle_parser_all(start=True)
    elif data_str == "parser:pause":
        text = _toggle_parser_all(start=False)
    else:
        _answer_callback(callback_id, "Неизвестная кнопка")
        return

    _edit_text(
        chat_id,
        message_id,
        text,
        reply_markup={"inline_keyboard": [[{"text": "🏠 На главную", "callback_data": "home"}]]},
    )
    _answer_callback(callback_id)


def _handle_settings_callback(callback_id: str, chat_id: int, message_id: int, data_str: str):
    if data_str == "settings:leadttl":
        _set_state(settings.owner_id, "set_lead_ttl")
        _send_message(chat_id, "Введи Lead TTL в часах (0 = отключить TTL).")
        _answer_callback(callback_id, "Жду число")
        return

    if data_str == "settings:joininterval":
        _set_state(settings.owner_id, "set_join_interval")
        _send_message(chat_id, "Введи интервал вступления в чаты в секундах.")
        _answer_callback(callback_id, "Жду число")
        return

    _answer_callback(callback_id, "Неизвестная кнопка")


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

                if data_str == "noop":
                    _answer_callback(callback_id)
                    continue

                if data_str == "home":
                    _edit_text(chat_id, message_id, _home_text(), reply_markup=_home_inline_keyboard())
                    _answer_callback(callback_id)
                    continue

                if data_str.startswith("bl:"):
                    parts = data_str.split(":")
                    if len(parts) != 3:
                        _answer_callback(callback_id, "Неизвестная кнопка")
                        continue

                    action = parts[1]
                    user_id = int(parts[2])
                    if action == "on":
                        is_bl = _set_blacklist(user_id, True)
                        _edit_keyboard(
                            chat_id,
                            message_id,
                            _build_toggled_keyboard(existing_keyboard, is_bl, user_id),
                        )
                        _answer_callback(callback_id, "Добавил в ЧС ✅")
                    elif action == "off":
                        is_bl = _set_blacklist(user_id, False)
                        _edit_keyboard(
                            chat_id,
                            message_id,
                            _build_toggled_keyboard(existing_keyboard, is_bl, user_id),
                        )
                        _answer_callback(callback_id, "Убрал из ЧС ✅")
                    else:
                        _answer_callback(callback_id, "Неизвестное действие")
                    continue

                if data_str.startswith("acc:"):
                    _handle_acc_callback(callback_id, chat_id, message_id, data_str)
                    continue

                if data_str.startswith("chat:"):
                    _handle_chat_callback(callback_id, chat_id, message_id, data_str)
                    continue

                if data_str.startswith("kw:"):
                    _handle_keywords_callback(callback_id, chat_id, message_id, data_str)
                    continue

                if data_str.startswith("res:"):
                    _handle_results_callback(callback_id, chat_id, message_id, data_str)
                    continue

                if data_str.startswith("stats:"):
                    _handle_stats_callback(callback_id, chat_id, message_id, data_str)
                    continue

                if data_str.startswith("parser:"):
                    _handle_parser_callback(callback_id, chat_id, message_id, data_str)
                    continue

                if data_str.startswith("settings:"):
                    _handle_settings_callback(callback_id, chat_id, message_id, data_str)
                    continue

                _answer_callback(callback_id, "Неизвестная кнопка")

        except Exception as e:
            logger.error(f"[BOT] updates loop error: {e}")
            time.sleep(2)
