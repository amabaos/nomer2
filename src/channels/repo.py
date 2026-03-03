from pathlib import Path
import json
from typing import List, Dict, Any

CHANNELS_FILE = Path("data/users/1/channels.json")

def _ensure_file():
    CHANNELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not CHANNELS_FILE.exists():
        CHANNELS_FILE.write_text("[]", encoding="utf-8")

def load_channels_raw() -> List[Dict[str, Any]]:
    _ensure_file()
    return json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))

def save_channels_raw(items: List[Dict[str, Any]]):
    CHANNELS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

def add_channel_entry(*, chat_id: int, username: str | None, title: str | None, active: bool = True):
    items = load_channels_raw()
    # не дублируем
    if any(it.get("id") == chat_id for it in items):
        return
    items.append({
        "id": chat_id,
        "username": username,
        "title": title or "",
        "active": active
    })
    save_channels_raw(items)
