import asyncio
import random
from loguru import logger
from pyrogram.errors import FloodWait

from src.gateways.telegram_client import make_client
from src.groups.repo import add_chat as groups_add_chat_repo
from src.join.repo import pick_next_task, mark_done, mark_failed, mark_floodwait, mark_join_result


class JoinWorker:
    def __init__(self, account, *, min_delay: float = 2.0, max_delay: float = 7.0, poll_seconds: float = 2.0):
        self.account = account
        self.min_delay = float(min_delay)
        self.max_delay = float(max_delay)
        self.poll_seconds = float(poll_seconds)

    async def run(self):
        aid = int(self.account.id)
        logger.info(f"[JOIN] worker started acc#{aid}")

        while True:
            task = pick_next_task(aid)
            if not task:
                await asyncio.sleep(self.poll_seconds)
                continue

            handle = task.handle
            group = task.group
            task_id = task.id

            try:
                async with make_client(self.account.session_path, self.account.proxy) as app:
                    status = "joined"
                    try:
                        await app.join_chat(handle)
                        logger.info(f"[JOIN] acc#{aid}: joined {handle}")
                    except Exception as e:
                        err = str(e).lower()
                        if "already" in err or "participant" in err:
                            status = "already"
                            logger.info(f"[JOIN] acc#{aid}: already in {handle}")
                        else:
                            logger.warning(f"[JOIN] acc#{aid}: join error {handle}: {e} (continue to get_chat)")

                    chat = await app.get_chat(handle)
                    if group:
                        groups_add_chat_repo(group, {
                            "id": chat.id,
                            "username": getattr(chat, "username", None),
                            "title": getattr(chat, "title", None) or "Без названия",
                            "active": True,
                        })

                    if status == "already":
                        # сохраняем как успешный терминальный статус
                        mark_join_result(
                            task_id,
                            status="already",
                            chat_id=chat.id,
                            username=getattr(chat, "username", None),
                            title=getattr(chat, "title", None),
                        )
                    else:
                        mark_done(task_id, chat.id, getattr(chat, "username", None), getattr(chat, "title", None))

                    logger.info(f"[JOIN] task#{task_id}: saved group={group} chat_id={chat.id}")

            except FloodWait as fw:
                seconds = int(getattr(fw, "value", None) or getattr(fw, "x", None) or getattr(fw, "seconds", 60))
                mark_floodwait(task_id, seconds)
                logger.warning(f"[JOIN] acc#{aid}: floodwait {seconds}s for task#{task_id}")
                await asyncio.sleep(seconds + 2)

            except Exception as e:
                mark_failed(task_id, str(e))
                logger.error(f"[JOIN] task#{task_id} failed: {e}")

            await asyncio.sleep(random.uniform(self.min_delay, self.max_delay))
