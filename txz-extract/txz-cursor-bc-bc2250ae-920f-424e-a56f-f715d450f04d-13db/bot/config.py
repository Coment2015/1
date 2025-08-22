from dataclasses import dataclass
import os


@dataclass
class Settings:
    bot_token: str


def get_settings() -> Settings:
    """Load settings from environment variables.

    Raises a clear error if a required variable is missing to avoid silent misconfiguration.
    """
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError(
            "Не найден переменный окружения BOT_TOKEN. Укажите токен в .env или в окружении."
        )
    return Settings(bot_token=bot_token)