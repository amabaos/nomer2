import threading
import time
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import text

from src.bot.callbacks import run_bot_updates_loop
from src.db.database import engine
from src.db.models import Base
from src.bot.callbacks import send_daily_results_report
from src.ui.cli import app


def init_storage() -> None:
    Base.metadata.create_all(bind=engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE lead_events ADD COLUMN message_id BIGINT"))
    except Exception:
        pass


def _daily_results_loop() -> None:
    while True:
        now = datetime.now()
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        sleep_for = max(1, int((next_midnight - now).total_seconds()))
        time.sleep(sleep_for)
        try:
            send_daily_results_report()
        except Exception as e:
            logger.error(f"daily report failed: {e}")


def start_callback_loop() -> threading.Thread:
    t = threading.Thread(target=run_bot_updates_loop, daemon=True)
    t.start()
    return t


def start_daily_report_loop() -> threading.Thread:
    t = threading.Thread(target=_daily_results_loop, daemon=True)
    t.start()
    return t


def main() -> None:
    init_storage()
    start_daily_report_loop()
    app()


if __name__ == "__main__":
    main()
