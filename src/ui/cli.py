import typer
from getpass import getpass
from pathlib import Path
import uuid
import threading

from src.leads.formatter import build_lead_message
from src.leads.notifier import send_lead_html

from src.accounts.repo import list_accounts, upsert_account, get_next_id, get_account
from src.accounts.models import Account

from src.storage.paths import account_session_path
from src.core.config import settings
from src.gateways.telegram_client import make_client

from src.channels.repo import add_channel_entry, load_channels_raw, save_channels_raw

from src.groups.repo import (
    create_group,
    set_group_enabled,
    load_groups,
    get_group,
    add_keyword as groups_add_keyword_repo,
    remove_keyword as groups_remove_keyword_repo,
    add_chat as groups_add_chat_repo,
    remove_chat as groups_remove_chat_repo,
)

from src.assignments.repo import (
    auto_assign_chat_ids_round_robin,
    apply_distribution,
    get_account_chat_ids,
)

from src.groups.runtime import collect_active_chat_ids

# JOIN
from src.join.models import JoinTask
from src.join.repo import add_tasks, stats as join_stats_repo, reset_all_to_queued
from src.join.service import run_join_workers
from src.stats_service import collect_system_stats
from src.db.database import engine
from src.db.models import Base
from src.bot.callbacks import run_bot_updates_loop


app = typer.Typer(help="ПАРСЕР — консольное управление (MVP)")


def _norm_handle(s: str) -> str:
    s = (s or "").strip()
    if not s:
        raise typer.BadParameter("handle пуст. Укажи @username или инвайт-ссылку полностью.")
    if s.startswith("https://t.me/"):
        return s
    if s.startswith("@"):
        return s
    if all(c.isalnum() or c == "_" for c in s):
        return "@" + s
    return s


def _parse_ids_csv(s: str) -> list[int]:
    out = []
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            raise typer.BadParameter(f"Неверный ID в списке: {part!r}. Нужно типа: 1,2,3")
    return out


# -------------------- TEST --------------------

@app.command("send-test")
def send_test(
    chat_title: str = typer.Option("🍋 Тут є робота | Київ", help="Название чата"),
    chat_username: str = typer.Option("example_channel", help="@юзернейм чата (без @)"),
    chat_id: str = typer.Option("-1001234567890", help="Числовой chat_id (как строку)"),
    author_username: str = typer.Option("Hrhelperhr1", help="@автор (без @)"),
    stage: str = typer.Option("Stage 3", help="Стейдж/группа (строка в тексте)"),
    text: str = typer.Option("Ищу работу, любую, офис + !", help="Текст лида"),
):
    try:
        chat_id_int = int(chat_id)
    except ValueError:
        typer.echo("Ошибка: chat_id должен быть числом.")
        raise typer.Exit(code=1)

    payload = build_lead_message(
        chat_title=chat_title,
        chat_username=chat_username or None,
        chat_id=chat_id_int,
        author_username=author_username or None,
        stage=stage,
        message_text=text,
        message_id=42,
    )

    typer.echo("\n=== Сообщение ===\n" + payload["text"])
    typer.echo("\n=== Кнопки ===")

    for btn in payload.get("buttons", []):
        if "url" in btn:
            typer.echo(f"{btn['text']} -> {btn['url']}")
        elif "callback_data" in btn:
            typer.echo(f"{btn['text']} -> {btn['callback_data']}")
        else:
            typer.echo(btn.get("text", "<?>"))


@app.command("send-telegram-test")
def send_telegram_test(
    account_id: int = typer.Option(..., help="ID аккаунта (пока только для проверки что он есть)"),
    chat_id: str = typer.Option(None, help="Куда слать (если пусто — SERVICE_CHAT_ID из .env)"),
    stage: str = typer.Option("Stage 1", help="Стейдж/группа для подписи в тексте"),
    text: str = typer.Option("Ищу работу, любую, офис + !", help="Текст тестового лида"),
    chat_title: str = typer.Option("Пример чата", help="Название чата для ссылки"),
    chat_username: str = typer.Option("example_channel", help="@юзернейм чата (без @, если есть)"),
    message_id: int = typer.Option(42, help="Тестовый ID сообщения (для ссылки)"),
    author_username: str = typer.Option("Hrhelperhr1", help="@автор (без @)"),
):
    acc = get_account(account_id)
    if not acc:
        typer.echo(f"Аккаунт #{account_id} не найден.")
        raise typer.Exit(code=1)

    target = int(chat_id) if chat_id else int(settings.service_chat_id or 0)
    if not target:
        typer.echo("Нужно указать --chat-id или прописать SERVICE_CHAT_ID в .env")
        raise typer.Exit(code=1)

    payload = build_lead_message(
        chat_title=chat_title,
        chat_username=chat_username or None,
        chat_id=target,
        author_username=author_username or None,
        stage=stage,
        message_text=text,
        message_id=message_id,
    )

    import asyncio
    asyncio.run(send_lead_html(
        chat_id=target,
        text_html=payload["text"],
        buttons=payload.get("buttons", []),
    ))

    typer.echo("Сообщение отправлено ✅")


# -------------------- ACCOUNTS --------------------

@app.command("accounts-add")
def accounts_add(
    phone: str = typer.Option(..., prompt=True, help="Номер телефона (с +...)"),
    stage: str = typer.Option("Stage 1", help="Метка аккаунта"),
    proxy: str = typer.Option(None, help="Прокси URL (http://user:pass@ip:port или socks5://...)"),
    username: str = typer.Option(None, help="@юзернейм без @ (необязательно)"),
    name: str = typer.Option(None, help="Имя в профиле (необязательно)"),
    max_chats: int = typer.Option(250, help="Лимит чатов на аккаунт (MVP дефолт 250)"),
):
    accs = list_accounts()
    new_id = get_next_id(accs)
    session_path = account_session_path(new_id)

    acc = Account(
        id=new_id,
        stage=stage,
        phone=phone,
        username=username,
        name=name,
        proxy=proxy,
        session_path=session_path,
        status="inactive",
        max_chats=int(max_chats),
    )

    from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid

    async def _login():
        client = make_client(session_path, proxy)
        await client.connect()

        sent = await client.send_code(phone)
        typer.echo("Код отправлен в Telegram. Введите его ниже:")
        code = typer.prompt("Код (например 12345)")

        try:
            await client.sign_in(
                phone_number=phone,
                phone_code_hash=sent.phone_code_hash,
                phone_code=code,
            )
        except SessionPasswordNeeded:
            pw = getpass("Включена двухфакторка. Пароль: ")
            await client.check_password(pw)
        except PhoneCodeInvalid:
            typer.echo("Неверный код. Повторите команду.")
            await client.disconnect()
            raise typer.Exit(code=1)

        acc.status = "active"
        await client.disconnect()

    import asyncio
    asyncio.run(_login())

    upsert_account(acc)
    typer.echo(f"Готово: аккаунт #{acc.id} добавлен. Файл сессии: {acc.session_path}")


@app.command("accounts-list")
def accounts_list():
    accs = list_accounts()
    if not accs:
        typer.echo("Пока нет аккаунтов.")
        return
    for a in accs:
        typer.echo(
            f"#{a.id} | {a.stage} | {a.phone} | status={a.status} | reason={getattr(a, 'status_reason', None)} | proxy={'yes' if a.proxy else 'no'} | max_chats={getattr(a, 'max_chats', 250)}"
        )




@app.command("accounts-set-status")
def accounts_set_status(
    account_id: int = typer.Option(...),
    status: str = typer.Option(..., help="active/paused/disabled/error/authorization_required/inactive"),
    reason: str = typer.Option(None, help="Причина/комментарий"),
):
    allowed = {"active", "paused", "disabled", "error", "authorization_required", "inactive"}
    status = (status or "").strip().lower()
    if status not in allowed:
        raise typer.BadParameter(f"Неверный статус: {status}")

    acc = get_account(account_id)
    if not acc:
        typer.echo("Аккаунт не найден")
        raise typer.Exit(code=1)

    acc.status = status
    acc.status_reason = reason
    upsert_account(acc)
    typer.echo("OK")
@app.command("accounts-set-maxchats")
def accounts_set_maxchats(
    account_id: int = typer.Option(...),
    max_chats: int = typer.Option(...),
):
    acc = get_account(account_id)
    if not acc:
        typer.echo("Аккаунт не найден")
        raise typer.Exit(code=1)

    acc.max_chats = int(max_chats)
    upsert_account(acc)
    typer.echo("OK")


# -------------------- RUN --------------------

@app.command("parser-run")
def parser_run():
    """Запускает все активные аккаунты и начинает парсинг."""
    import asyncio
    from src.leads.pipeline import run_all_workers
    Base.metadata.create_all(bind=engine)
    asyncio.run(run_all_workers())


@app.command("bot-run")
def bot_run():
    """Запуск bot long-polling (команды + callback)."""
    Base.metadata.create_all(bind=engine)
    run_bot_updates_loop()


@app.command("run")
def run_all():
    """Полный запуск: bot-loop в фоне + parser в основном потоке."""
    Base.metadata.create_all(bind=engine)
    t = threading.Thread(target=run_bot_updates_loop, daemon=True)
    t.start()

    import asyncio
    from src.leads.pipeline import run_all_workers
    asyncio.run(run_all_workers())


# -------------------- TELEGRAM UTILS --------------------

@app.command("resolve")
def resolve_chat(
    account_id: int = typer.Option(..., help="ID аккаунта"),
    handle: str = typer.Option(..., help="@username или инвайт-ссылка (https://t.me/...)"),
):
    acc = get_account(account_id)
    if not acc:
        typer.echo("Аккаунт не найден")
        raise typer.Exit(code=1)

    handle = _norm_handle(handle)

    async def _do():
        async with make_client(acc.session_path, acc.proxy) as app_cli:
            chat = await app_cli.get_chat(handle)
            typer.echo(f"title: {chat.title}")
            typer.echo(f"username: {getattr(chat, 'username', None)}")
            typer.echo(f"chat_id: {chat.id}")

    import asyncio
    asyncio.run(_do())


@app.command("join")
def join_chat(
    account_id: int = typer.Option(..., help="ID аккаунта"),
    handle: str = typer.Option(..., help="@username или инвайт-ссылка (https://t.me/...)"),
):
    acc = get_account(account_id)
    if not acc:
        typer.echo("Аккаунт не найден")
        raise typer.Exit(code=1)

    handle = _norm_handle(handle)

    async def _do():
        async with make_client(acc.session_path, acc.proxy) as app_cli:
            try:
                await app_cli.join_chat(handle)
                typer.echo("OK: joined")
            except Exception as e:
                typer.echo(f"Не удалось join (возможно уже участник): {e}")
                chat = await app_cli.get_chat(handle)
                typer.echo(f"Доступ есть. chat_id={chat.id}, title={chat.title}")

    import asyncio
    asyncio.run(_do())


@app.command("debug-history")
def debug_history(
    account_id: int = typer.Option(..., help="ID аккаунта"),
    chat_id: int = typer.Option(..., help="Числовой chat_id (например -1003046804594)"),
    limit: int = typer.Option(10, help="Сколько последних сообщений показать"),
):
    acc = get_account(account_id)
    if not acc:
        typer.echo("Аккаунт не найден")
        raise typer.Exit(code=1)

    async def _do():
        async with make_client(acc.session_path, acc.proxy) as app_cli:
            typer.echo(f"Последние {limit} сообщений из чата {chat_id}:")
            async for msg in app_cli.get_chat_history(chat_id, limit=limit):
                author = getattr(msg.from_user, "username", None) if msg.from_user else None
                text = (msg.text or "").replace("\n", " ")
                if len(text) > 60:
                    text = text[:57] + "..."
                typer.echo(f"[id={msg.id}] @{author} -> {text!r}")

    import asyncio
    asyncio.run(_do())


# -------------------- LEGACY CHANNELS --------------------

@app.command("channels-add")
def channels_add(
    account_id: int = typer.Option(..., help="ID аккаунта, через который резолвим"),
    handle: str = typer.Option(..., help="@username или инвайт-ссылка"),
    active: bool = typer.Option(True, help="Активировать сразу"),
):
    acc = get_account(account_id)
    if not acc:
        typer.echo("Аккаунт не найден")
        raise typer.Exit(code=1)

    handle = _norm_handle(handle)

    async def _do():
        async with make_client(acc.session_path, acc.proxy) as app_cli:
            chat = await app_cli.get_chat(handle)
            add_channel_entry(
                chat_id=chat.id,
                username=getattr(chat, "username", None),
                title=chat.title,
                active=active,
            )
            typer.echo(f"Добавлено: {chat.title} (id={chat.id}, @{getattr(chat, 'username', None)})")

    import asyncio
    asyncio.run(_do())


@app.command("channels-list")
def channels_list():
    items = load_channels_raw()
    if not items:
        typer.echo("Список пуст.")
        return
    for it in items:
        typer.echo(f"id={it['id']} | @{it.get('username')} | {it.get('title')} | active={it.get('active', True)}")


@app.command("channels-remove")
def channels_remove(chat_id: int = typer.Option(..., help="id чата для удаления")):
    items = load_channels_raw()
    items = [it for it in items if it.get("id") != chat_id]
    save_channels_raw(items)
    typer.echo(f"Удалён id={chat_id}")


# -------------------- GROUPS --------------------

@app.command("groups-create")
def groups_create(name: str = typer.Option(..., help="Имя группы, например KYIV_WORK")):
    create_group(name)
    typer.echo(f"OK: group created: {name}")


@app.command("groups-enable")
def groups_enable(
    name: str = typer.Option(..., help="Имя группы"),
    enabled: bool = typer.Option(True, help="true/false"),
):
    set_group_enabled(name, enabled)
    typer.echo("OK")


@app.command("groups-list")
def groups_list():
    gs = load_groups()
    if not gs:
        typer.echo("Групп пока нет.")
        return
    for g in gs:
        typer.echo(
            f"{g['name']} | enabled={g.get('enabled', True)} | "
            f"keywords={len(g.get('keywords', []))} | chats={len(g.get('chats', []))}"
        )


@app.command("groups-show")
def groups_show(name: str = typer.Option(..., help="Имя группы")):
    g = get_group(name)
    if not g:
        typer.echo("Группа не найдена")
        raise typer.Exit(code=1)

    typer.echo(f"== {g['name']} ==")
    typer.echo(f"enabled: {g.get('enabled', True)}")

    typer.echo("keywords:")
    for kw in g.get("keywords", []):
        typer.echo(f"  - {kw}")

    typer.echo("chats:")
    for c in g.get("chats", []):
        typer.echo(f"  - id={c.get('id')} | @{c.get('username')} | {c.get('title')} | active={c.get('active', True)}")


@app.command("groups-add-keyword")
def groups_add_keyword(
    group: str = typer.Option(..., help="Имя группы"),
    phrase: str = typer.Option(..., help="Фраза/ключевик"),
):
    groups_add_keyword_repo(group, phrase)
    typer.echo("OK: keyword added")


@app.command("groups-remove-keyword")
def groups_remove_keyword(
    group: str = typer.Option(..., help="Имя группы"),
    phrase: str = typer.Option(..., help="Фраза/ключевик"),
):
    groups_remove_keyword_repo(group, phrase)
    typer.echo("OK: keyword removed")


@app.command("groups-add-chat")
def groups_add_chat(
    account_id: int = typer.Option(..., help="ID аккаунта, через который резолвим"),
    group: str = typer.Option(..., help="Имя группы"),
    handle: str = typer.Option(..., help="@username или инвайт-ссылка"),
    active: bool = typer.Option(True, help="Активировать сразу"),
):
    acc = get_account(account_id)
    if not acc:
        typer.echo("Аккаунт не найден")
        raise typer.Exit(code=1)

    handle = _norm_handle(handle)

    async def _do():
        async with make_client(acc.session_path, acc.proxy) as app_cli:
            chat = await app_cli.get_chat(handle)
            groups_add_chat_repo(group, {
                "id": chat.id,
                "username": getattr(chat, "username", None),
                "title": chat.title,
                "active": active,
            })
            typer.echo(f"OK: added chat to group {group}: {chat.title} (id={chat.id})")

    import asyncio
    asyncio.run(_do())


@app.command("groups-remove-chat")
def groups_remove_chat(
    group: str = typer.Option(..., help="Имя группы"),
    chat_id: int = typer.Option(..., help="chat_id"),
):
    groups_remove_chat_repo(group, chat_id)
    typer.echo("OK: chat removed")


# -------------------- ASSIGNMENTS --------------------

@app.command("assign-auto-by-group")
def assign_auto_by_group(
    group: str = typer.Option(..., help="Имя группы"),
    accounts: str = typer.Option(..., help="Список аккаунтов через запятую, например 1,2,3"),
    mode: str = typer.Option("replace", help="append/replace"),
):
    # берём активные чаты из групп.json по имени
    g = get_group(group)
    if not g:
        typer.echo("Группа не найдена")
        raise typer.Exit(code=1)

    chat_ids = [int(c["id"]) for c in g.get("chats", []) if c.get("active", True)]
    acc_ids = _parse_ids_csv(accounts)

    dist = auto_assign_chat_ids_round_robin(acc_ids, chat_ids)
    apply_distribution(dist, mode=mode)

    typer.echo("OK: распределено")
    for a, lst in dist.items():
        typer.echo(f"acc#{a}: {len(lst)} чатов")


@app.command("assign-list")
def assign_list(
    account_id: int = typer.Option(..., help="ID аккаунта"),
):
    ids = get_account_chat_ids(account_id)
    if not ids:
        typer.echo("Пусто (нет назначений). Тогда по умолчанию парсит ALL (все активные чаты из групп).")
        return
    typer.echo(f"account #{account_id} чат(ов): {len(ids)}")
    for cid in ids:
        typer.echo(str(cid))


# -------------------- JOIN QUEUE (MVP) --------------------

@app.command("join-import")
def join_import(
    file: str = typer.Option(..., help="Путь к файлу со списком чатов (по строкам)"),
    group: str = typer.Option(None, help="Опционально: имя группы, чтобы пометить задачи"),
):
    p = Path(file)
    if not p.exists():
        typer.echo("Файл не найден")
        raise typer.Exit(code=1)

    lines = []
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(_norm_handle(s))

    tasks = [
        JoinTask(id=str(uuid.uuid4()), group=group, handle=h)
        for h in lines
    ]
    add_tasks(tasks)
    typer.echo(f"OK: импортировано задач: {len(tasks)}")


@app.command("join-stats")
def join_stats():
    s = join_stats_repo()
    typer.echo(f"total={s.get('total', 0)}")
    for k in ["queued", "in_progress", "joined", "already", "pending_request", "floodwait", "failed"]:
        typer.echo(f"{k}={s.get(k, 0)}")


@app.command("join-reset")
def join_reset():
    reset_all_to_queued()
    typer.echo("OK: reset (in_progress/floodwait/failed -> queued)")


@app.command("join-run")
def join_run(
    accounts: str = typer.Option(..., help="Список аккаунтов через запятую: 1,2,3,4"),
    min_delay: float = typer.Option(2.0, help="Минимальная задержка между join на аккаунт (сек)"),
    max_delay: float = typer.Option(7.0, help="Максимальная задержка между join на аккаунт (сек)"),
):
    acc_ids = _parse_ids_csv(accounts)

    existing = {a.id: a for a in list_accounts()}
    missing = [a for a in acc_ids if a not in existing]
    if missing:
        raise typer.BadParameter(f"Не найдены аккаунты: {missing}")

    inactive = [a for a in acc_ids if existing[a].status != "active"]
    if inactive:
        raise typer.BadParameter(f"Аккаунты не active: {inactive}. Сначала accounts-add / активируй.")

    import asyncio
    asyncio.run(run_join_workers(acc_ids, min_delay=min_delay, max_delay=max_delay))

@app.command("stats")
def stats_system():
    s = collect_system_stats()
    typer.echo("=== SYSTEM STATS ===")
    typer.echo(f"accounts_total={s['accounts_total']}")
    typer.echo(f"accounts_active={s['accounts_active']}")
    typer.echo(f"accounts_paused={s['accounts_paused']}")
    typer.echo(f"accounts_disabled={s['accounts_disabled']}")
    typer.echo(f"accounts_error={s['accounts_error']}")
    typer.echo(f"chats_monitored={s['chats_monitored']}")
    typer.echo(f"leads_sent_total={s['leads_sent_total']}")
    typer.echo(f"blacklist_total={s['blacklist_total']}")
    js = s['join']
    typer.echo(f"join_total={js.get('total', 0)}")
    for k in ["queued", "in_progress", "joined", "already", "pending_request", "floodwait", "failed"]:
        typer.echo(f"join_{k}={js.get(k, 0)}")
