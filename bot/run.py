"""Запуск бота: python -m bot.run  (из корня репозитория)"""
import asyncio

import aiogram
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from . import config as cfg
from .handlers import router


async def main():
    if not cfg.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN пуст — вписать в .env (рядом: GITHUB_TOKEN=GITHUB PAT)")
    bot = Bot(token=cfg.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    me = await bot.get_me()
    url = (await bot.get_webhook_info()).url
    if url:
        await bot.delete_webhook(drop_pending_updates=False)
    print(f"Bot @{me.username} запущен · каталог: {len(_products())} товаров · GitHub sync: {'on' if cfg.GITHUB_TOKEN else 'local'}")
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()


def _products():
    from . import storage
    return storage.products()


if __name__ == "__main__":
    asyncio.run(main())
