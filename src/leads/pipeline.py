import asyncio
from loguru import logger

from src.accounts.repo import list_accounts, upsert_account
from src.parsing.worker import ParserWorker

from src.assignments.repo import get_account_chat_ids
from src.groups.runtime import collect_active_chat_ids


async def _run_worker_guarded(acc, channels):
    try:
        w = ParserWorker(acc, channels)
        await w.run()
    except Exception as e:
        logger.error(f"acc#{acc.id} worker crashed: {e}")
        acc.status = "error"
        acc.status_reason = str(e)[:300]
        upsert_account(acc)


async def run_all_workers():
    accounts = [a for a in list_accounts() if a.status == "active"]
    if not accounts:
        logger.warning("Нет активных аккаунтов для парсинга.")
        return

    tasks = []
    for acc in accounts:
        assigned = get_account_chat_ids(acc.id)
        channels = assigned if assigned else collect_active_chat_ids()

        if not channels:
            logger.warning(f"acc#{acc.id}: нет чатов для парсинга — пропуск")
            continue

        logger.info(f"acc#{acc.id} | chats={len(channels)} | assigned={'yes' if assigned else 'no (ALL)'}")
        tasks.append(asyncio.create_task(_run_worker_guarded(acc, channels)))

    if not tasks:
        logger.warning("Нет задач для запуска (проверь группы/чаты/assignments).")
        return

    await asyncio.gather(*tasks, return_exceptions=True)
