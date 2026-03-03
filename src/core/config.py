from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: str
    service_chat_id: int
    api_id: int
    api_hash: str
    owner_id: int
    database_url: str
    lead_ttl_hours: int = 24

    class Config:
        env_file = ".env"
        extra = "ignore"  # чтобы не падало от лишних переменных


settings = Settings()