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

    # старт может упасть, если сеть к api.telegram.org временно недоступна
    # (VPN провалился, провайдер трясётся) — ждём и пробуем снова.
    attempt, me = 0, None
    while me is None:
        try:
            me = await bot.get_me()
            url = (await bot.get_webhook_info()).url
            if url:
                await bot.delete_webhook(drop_pending_updates=False)
        except Exception as e:
            attempt += 1
            wait = min(60, 5 * attempt)
            print(f"[net] Telegram недоступен ({e.__class__.__name__}), повтор через {wait} c")
            await asyncio.sleep(wait)
    print(f"Bot @{me.username} запущен · каталог: {len(_products())} товаров · GitHub sync: {'on' if cfg.GITHUB_TOKEN else 'local'}")

    def _status(mode: str) -> None:
        from . import storage
        r = storage.set_bot_status(mode == "up")
        if r == "github":
            print(f"[status] сайт обновлён: бот {mode}")

    _status("up")
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        _status("down")
        await bot.session.close()


def _products():
    from . import storage
    return storage.products()


if __name__ == "__main__":
    asyncio.run(main())
