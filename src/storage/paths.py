from pathlib import Path

def account_dir(account_id: int) -> Path:
    p = Path(f"data/users/1/accounts/{account_id}")
    p.mkdir(parents=True, exist_ok=True)
    return p

def account_session_path(account_id: int) -> str:
    return str(account_dir(account_id) / "session.session")
