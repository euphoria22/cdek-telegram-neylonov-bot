import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Публичные тестовые ключи СДЭК (из документации API v2) для контура api.edu.cdek.ru
TEST_CLIENT_ID = "wqGwiQx0gg8mLtiEKsUinjVSICCjtTEP"
TEST_CLIENT_SECRET = "RmAmgvSgSl1yirlz9QupbzOJVqhCxcP5"


@dataclass
class Config:
    bot_token: str
    cdek_client_id: str
    cdek_client_secret: str
    cdek_base_url: str
    allowed_users: set[int]
    shared_templates: bool
    db_path: str
    redis_url: str | None
    telegram_api_url: str | None = None
    telegram_proxy: str | None = None


def load_config() -> Config:
    load_dotenv()
    test = os.getenv("CDEK_TEST", "1") == "1"
    client_id = os.getenv("CDEK_CLIENT_ID", "").strip()
    secret = os.getenv("CDEK_CLIENT_SECRET", "").strip()
    if test and not client_id:
        client_id, secret = TEST_CLIENT_ID, TEST_CLIENT_SECRET
    if not client_id or not secret:
        raise RuntimeError("Задайте CDEK_CLIENT_ID и CDEK_CLIENT_SECRET")
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Задайте BOT_TOKEN")
    allowed = {int(x) for x in os.getenv("ALLOWED_USERS", "").replace(" ", "").split(",") if x}
    return Config(
        bot_token=token,
        cdek_client_id=client_id,
        cdek_client_secret=secret,
        cdek_base_url="https://api.edu.cdek.ru/v2" if test else "https://api.cdek.ru/v2",
        allowed_users=allowed,
        shared_templates=os.getenv("SHARED_TEMPLATES", "0") == "1",
        db_path=os.getenv("DB_PATH", "cdek_bot.sqlite3"),
        redis_url=os.getenv("REDIS_URL") or None,
        telegram_api_url=(os.getenv("TELEGRAM_API_URL") or "").strip().rstrip("/") or None,
        telegram_proxy=(os.getenv("TELEGRAM_PROXY") or "").strip() or None,
    )
