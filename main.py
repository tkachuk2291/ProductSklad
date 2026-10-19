import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from bot.config import load_config
from bot.db.session import create_engine, init_db
from bot.logsetup import aiogram_middleware, setup_logging, watch_loop
from bot.handlers import expiry, group, products, start, warehouses
from bot.middlewares import DbSessionMiddleware
from bot.services import dates_ocr


async def main() -> None:
    setup_logging("sklad-bot")
    watch_loop()
    config = load_config()

    engine = create_engine(config.database_url)
    session_pool = await init_db(engine)

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage(), config=config)
    dp.update.outer_middleware(aiogram_middleware())
    dp.update.outer_middleware(DbSessionMiddleware(session_pool))
    dp.include_routers(start.router, warehouses.router, products.router, expiry.router, group.router)

    await bot.set_my_commands(scope=BotCommandScopeAllPrivateChats(), commands=[
        BotCommand(command="sklad", description="Склады: выбрать / создать / изменить"),
        BotCommand(command="items", description="Товары выбранного склада"),
        BotCommand(command="add", description="Добавить товар"),
        BotCommand(command="expiry", description="Поиск по сроку годности"),
        BotCommand(command="cancel", description="Отменить действие"),
    ])
    await bot.set_my_commands(scope=BotCommandScopeAllGroupChats(), commands=[
        BotCommand(command="sklad", description="Склады и товары"),
    ])

    warmup = asyncio.create_task(asyncio.to_thread(dates_ocr.warmup))

    try:
        await dp.start_polling(bot)
    finally:
        warmup.cancel()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
