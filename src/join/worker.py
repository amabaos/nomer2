import asyncio
import time
from typing import Dict, List, Optional

from loguru import logger
from pyrogram.errors import FloodWait

from src.accounts.repo import get_account
from src.gateways.telegram_client import make_client
from src.groups.repo import add_chat as groups_add_chat_repo
from src.join.repo import pick_next_task, mark_done, mark_failed
from src.runtime_settings import load_runtime_settings


def _norm_handle(line: str) -> Optional[str]:
    s = (line or "").strip()
    if not s:
        return None

    # убираем мусорные пробелы/кавычки
    s = s.strip().strip('"').strip("'")

    if s.startswith("https://t.me/"):
        return s
    if s.startswith("@"):
        return s
    if s.startswith("t.me/"):
        return "https://" + s

    # обычный username без @
    if all(c.isalnum() or c == "_" for c in s):
        return "@" + s

    return s  # оставим как есть, Pyrogram сам скажет что не так


class JoinRunner:
    """
    Берет queued задачи из join_queue.json и пытается вступить в чаты через несколько аккаунтов.
    Важное: если FloodWait — аккаунт "замораживается", а задачи продолжают делать другие.
    """

    def __init__(self, account_ids: List[int], poll_seconds: int = 2):
        self.account_ids = [int(x) for x in account_ids]
        self.poll_seconds = poll_seconds
        self.cooldowns: Dict[int, float] = {aid: 0.0 for aid in self.account_ids}  # unix ts

    def _pick_available_account(self) -> Optional[int]:
        now = time.time()
        for aid in self.account_ids:
            if self.cooldowns.get(aid, 0.0) <= now:
                return aid
        return None

    async def run(self):
        logger.info(f"[JOIN] runner started. accounts={self.account_ids}")

        while True:
            aid = self._pick_available_account()
            if not aid:
                # все в cooldown — подождем чуть
                await asyncio.sleep(self.poll_seconds)
                continue

            task = pick_next_task(aid)
            if not task:
                # задач нет
                await asyncio.sleep(self.poll_seconds)
                continue

            handle = task.get("handle")
            group = task.get("group")
            task_id = task.get("id")

            acc = get_account(aid)
            if not acc:
                mark_failed(task_id, f"account #{aid} not found")
                continue

            try:
                async with make_client(acc.session_path, acc.proxy) as app:
                    # 1) join (может быть "already participant" — это тоже ок)
                    try:
                        await app.join_chat(handle)
                        logger.info(f"[JOIN] acc#{aid}: joined {handle}")
                    except Exception as e:
                        # если уже участник или join не обязателен (например публичный канал) — пробуем дальше
                        logger.warning(f"[JOIN] acc#{aid}: join error {handle}: {e} (continue to get_chat)")

                    # 2) резолвим чат и сохраняем в группу
                    chat = await app.get_chat(handle)
                    groups_add_chat_repo(group, {
                        "id": chat.id,
                        "username": getattr(chat, "username", None),
                        "title": getattr(chat, "title", None) or "Без названия",
                        "active": True,
                    })

                    mark_done(task_id, chat.id, getattr(chat, "username", None), getattr(chat, "title", None))
                    logger.info(f"[JOIN] task#{task_id}: saved to group={group} chat_id={chat.id}")

            except FloodWait as fw:
                # Pyrogram FloodWait.seconds
                seconds = int(getattr(fw, "value", None) or getattr(fw, "x", None) or getattr(fw, "seconds", 60))
                self.cooldowns[aid] = time.time() + seconds + 2
                mark_failed(task_id, f"FloodWait {seconds}s", status="queued")
                logger.warning(f"[JOIN] acc#{aid} floodwait {seconds}s, cooldown set")

            except Exception as e:
                mark_failed(task_id, str(e), status="queued")
                logger.error(f"[JOIN] task#{task_id} failed: {e}")

            interval = int(load_runtime_settings().get("join_interval_seconds", self.poll_seconds))
            await asyncio.sleep(max(1, interval))
