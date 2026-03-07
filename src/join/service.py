from typing import List

from loguru import logger

from src.accounts.repo import get_account
from src.join.worker import JoinRunner


async def run_join_workers(account_ids: List[int], *, min_delay: float = 2.0, max_delay: float = 7.0):
    accounts = []
    for aid in account_ids:
        a = get_account(int(aid))
        if not a:
            logger.warning(f"[JOIN] acc#{aid} не найден — пропуск")
            continue
        if a.status != "active":
            logger.warning(f"[JOIN] acc#{aid} status={a.status} — пропуск")
            continue
        accounts.append(a)

    if not accounts:
        logger.warning("[JOIN] нет активных аккаунтов для join")
        return

    runner = JoinRunner([a.id for a in accounts], poll_seconds=max(1, int(min_delay)))
    await runner.run()
