import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from bot.config import get_settings
from bot.handlers import router


# Enable uvloop on Unix if available for performance
try:
    import uvloop  # type: ignore

    uvloop.install()
except Exception:
    pass


async def on_startup(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запуск бота"),
            BotCommand(command="help", description="Справка"),
        ]
    )
    logging.info("Бот запущен и команды установлены")


async def on_shutdown() -> None:
    logging.info("Остановка бота")


async def main() -> None:
    # Load environment variables from a .env file if present
    load_dotenv()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    settings = get_settings()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Register lifecycle hooks
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Routers
    dp.include_router(router)

    # Start polling
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Получен сигнал остановки")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())