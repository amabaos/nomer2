from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime


JoinStatus = Literal[
    "queued",            # в очереди
    "in_progress",       # взято воркером
    "joined",            # вступил
    "already",           # уже участник
    "pending_request",   # отправлен запрос на вступление
    "failed",            # ошибка (невалид/бан/и т.д.)
    "floodwait",         # флудвейт (ожидание)
]


class JoinTask(BaseModel):
    id: str                              # uuid
    group: Optional[str] = None          # опционально, к какой группе относится
    handle: str                          # @username / https://t.me/... / https://t.me/+...
    status: JoinStatus = "queued"

    # кто обработал
    assigned_account_id: Optional[int] = None

    # ретраи/планирование
    tries: int = 0
    next_try_at: Optional[datetime] = None

    # результат/диагностика
    resolved_chat_id: Optional[int] = None
    resolved_title: Optional[str] = None
    resolved_username: Optional[str] = None

    last_error: Optional[str] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.utcnow())