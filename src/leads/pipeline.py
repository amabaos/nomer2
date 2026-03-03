import asyncio
from loguru import logger

from src.accounts.repo import list_accounts
from src.parsing.worker import ParserWorker

from src.assignments.repo import get_account_chat_ids
from src.groups.runtime import collect_active_chat_ids


async def run_all_workers():
    accounts = [a for a in list_accounts() if a.status == "active"]
    if not accounts:
        logger.warning("Нет активных аккаунтов для парсинга.")
        return

    tasks = []
    for acc in accounts:
        assigned = get_account_chat_ids(acc.id)

        # Безопасный дефолт: если назначений нет — парсим все активные чаты из groups.json
        channels = assigned if assigned else collect_active_chat_ids()

        if not channels:
            logger.warning(f"acc#{acc.id}: нет чатов для парсинга — пропуск")
            continue

        logger.info(f"acc#{acc.id} | chats={len(channels)} | assigned={'yes' if assigned else 'no (ALL)'}")

        w = ParserWorker(acc, channels)
        tasks.append(asyncio.create_task(w.run()))

    if not tasks:
        logger.warning("Нет задач для запуска (проверь группы/чаты/assignments).")
        return

    await asyncio.gather(*tasks)