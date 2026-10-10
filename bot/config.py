import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    database_url: str
    page_size: int


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "")
    if not token or token.startswith("123456:"):
        raise RuntimeError("Укажи настоящий BOT_TOKEN: в файле .env или в переменных окружения (в Portainer — Environment variables стека)")
    return Config(
        bot_token=token,
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///warehouse.db"),
        page_size=int(os.getenv("PAGE_SIZE", "10")),
    )
