from src.ui.cli import app  # абсолютный импорт (абсолютный — с полным путём пакета)
from src.db.database import engine
from src.db.models import Base
import threading
from src.bot.callbacks import run_bot_updates_loop

t = threading.Thread(target=run_bot_updates_loop, daemon=True)
t.start()

Base.metadata.create_all(bind=engine)

if __name__ == "__main__":
    app()