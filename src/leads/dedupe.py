from __future__ import annotations
import sqlite3, time, hashlib, asyncio
from pathlib import Path
from typing import Optional
import time as _time

DEFAULT_DB_PATH = Path("data/dedupe.sqlite")


class DedupeGuard:
    """
    Анти-дубль: 1 сообщение от 1 автора за N часов.
    Защита от ошибки 'database is locked' — используется глобальный async lock.
    """

    _lock = asyncio.Lock()  # глобальная блокировка на запись

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA busy_timeout=10000;")
        return con

    def _init_db(self):
        with self._connect() as con:
            con.execute("""
            CREATE TABLE IF NOT EXISTS last_seen (
                account_id INTEGER NOT NULL,
                author_key TEXT NOT NULL,
                ts INTEGER NOT NULL,
                PRIMARY KEY (account_id, author_key)
            );
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_last_seen_ts ON last_seen(ts);")
            con.commit()

    @staticmethod
    def _author_key(
        author_id: Optional[int],
        author_username: Optional[str],
        fallback_text: Optional[str],
        chat_id: Optional[int],
    ) -> str:
        if author_id:
            return f"id:{author_id}"
        if author_username:
            return f"usr:{author_username.lower()}"
        base = (fallback_text or "") + ":" + str(chat_id or 0)
        return "anon:" + hashlib.sha1(base.encode("utf-8")).hexdigest()

    async def should_allow(
        self,
        *,
        account_id: int,
        window_hours: int,
        author_id: Optional[int],
        author_username: Optional[str],
        fallback_text: Optional[str],
        chat_id: Optional[int],
        now_ts: Optional[int] = None,
    ) -> bool:
        """
        True — можно пускать лид. False — дубль в окне.
        """
        async with self._lock:
            now = int(now_ts or time.time())
            window_sec = int(window_hours * 3600)
            key = self._author_key(author_id, author_username, fallback_text, chat_id)

            for attempt in range(5):
                try:
                    with self._connect() as con:
                        row = con.execute(
                            "SELECT ts FROM last_seen WHERE account_id=? AND author_key=?",
                            (account_id, key),
                        ).fetchone()
                        if row and now - int(row[0]) < window_sec:
                            return False

                        con.execute(
                            "INSERT INTO last_seen (account_id, author_key, ts) VALUES (?, ?, ?) "
                            "ON CONFLICT(account_id, author_key) DO UPDATE SET ts=excluded.ts",
                            (account_id, key, now),
                        )
                        con.commit()
                    break
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and attempt < 4:
                        _time.sleep(0.2 * (attempt + 1))
                        continue
                    else:
                        raise
            return True

    def cleanup(self, older_than_hours: int = 24 * 7):
        threshold = int(time.time()) - int(older_than_hours * 3600)
        with self._connect() as con:
            con.execute("DELETE FROM last_seen WHERE ts < ?", (threshold,))
            con.commit()
