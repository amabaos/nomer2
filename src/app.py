import threading

from src.bot.callbacks import run_bot_updates_loop
from src.db.database import engine
from src.db.models import Base
from src.ui.cli import app


def init_storage() -> None:
    Base.metadata.create_all(bind=engine)


def start_callback_loop() -> threading.Thread:
    t = threading.Thread(target=run_bot_updates_loop, daemon=True)
    t.start()
    return t


def main() -> None:
    init_storage()
    app()


if __name__ == "__main__":
    main()
