import json
from pathlib import Path
from typing import Dict, Any

SETTINGS_FILE = Path("data/users/1/runtime_settings.json")

DEFAULTS: Dict[str, Any] = {
    "lead_ttl_hours": 24,
    "join_interval_seconds": 30,
}


def _ensure_file() -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_FILE.exists():
        SETTINGS_FILE.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")


def load_runtime_settings() -> Dict[str, Any]:
    _ensure_file()
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8") or "{}")
    except Exception:
        data = {}
    out = dict(DEFAULTS)
    out.update(data or {})
    out["lead_ttl_hours"] = max(0, int(out.get("lead_ttl_hours", DEFAULTS["lead_ttl_hours"])))
    out["join_interval_seconds"] = max(1, int(out.get("join_interval_seconds", DEFAULTS["join_interval_seconds"])))
    return out


def save_runtime_settings(new_values: Dict[str, Any]) -> Dict[str, Any]:
    cur = load_runtime_settings()
    cur.update(new_values or {})
    cur["lead_ttl_hours"] = max(0, int(cur.get("lead_ttl_hours", DEFAULTS["lead_ttl_hours"])))
    cur["join_interval_seconds"] = max(1, int(cur.get("join_interval_seconds", DEFAULTS["join_interval_seconds"])))
    SETTINGS_FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return cur
