from typing import Optional
from pyrogram import Client
from urllib.parse import urlparse
from src.core.config import settings

def _proxy_dict(proxy_url: Optional[str]) -> Optional[dict]:
    if not proxy_url:
        return None
    u = urlparse(proxy_url)
    scheme = (u.scheme or "").lower()
    return {
        "scheme": "socks5" if "socks" in scheme else "http",
        "hostname": u.hostname,
        "port": int(u.port),
        "username": u.username,
        "password": u.password,
    } if (u.hostname and u.port) else None

def make_client(session_path: str, proxy_url: Optional[str] = None, *, no_updates: bool = True) -> Client:
    if not settings.api_id or not settings.api_hash:
        raise RuntimeError("В .env должны быть API_ID и API_HASH")
    return Client(
        name=session_path,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        workdir=".",
        proxy=_proxy_dict(proxy_url),
        no_updates=no_updates,
        in_memory=False,
    )
