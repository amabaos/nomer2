from pydantic import BaseModel, Field
from typing import Optional, Literal


AccountStatus = Literal[
    "active",
    "paused",
    "disabled",
    "error",
    "authorization_required",
    "inactive",
]


class Account(BaseModel):
    id: int
    stage: str = Field(default="Stage 1")
    phone: str
    username: Optional[str] = None
    name: Optional[str] = None
    proxy: Optional[str] = None
    session_path: str
    status: AccountStatus = "inactive"
    status_reason: Optional[str] = None

    dedupe_enabled: bool = True
    dedupe_window_hours: int = 24
    max_chats: int = 250
