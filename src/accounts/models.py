from pydantic import BaseModel, Field
from typing import Optional


class Account(BaseModel):
    id: int
    stage: str = Field(default="Stage 1")              # метка (пока оставляем)
    phone: str
    username: Optional[str] = None
    name: Optional[str] = None
    proxy: Optional[str] = None
    session_path: str
    status: str = "inactive"

    dedupe_enabled: bool = True
    dedupe_window_hours: int = 24

    # NEW: лимит чатов на аккаунт (твой MVP-дефолт)
    max_chats: int = 250