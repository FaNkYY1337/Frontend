from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import get_settings
from app.db import Database
from app.handlers import admin, user
from app.security import SessionCipher
from app.services.listings import poll_gift_market
from app.services.subscriptions import revoke_expired_access


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings = get_settings()
    bot = Bot(settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    db = Database(settings)
    cipher = SessionCipher(settings.session_secret)

    await db.create_all()
    dp.include_router(admin.router)
    dp.include_router(user.router)

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    background_tasks = [
        asyncio.create_task(_subscription_worker(stop_event, bot, db, settings)),
        asyncio.create_task(_gift_market_worker(stop_event, bot, db, settings, cipher)),
    ]

    try:
        await dp.start_polling(bot, db=db, settings=settings, cipher=cipher)
    finally:
        stop_event.set()
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with suppress(asyncio.CancelledError):
                await task
        await bot.session.close()


async def _subscription_worker(stop_event: asyncio.Event, bot: Bot, db: Database, settings) -> None:
    while not stop_event.is_set():
        try:
            await revoke_expired_access(bot, db, settings)
        except Exception:
            logging.exception("Subscription worker failed")
        await _sleep_or_stop(stop_event, 60)


async def _gift_market_worker(
    stop_event: asyncio.Event,
    bot: Bot,
    db: Database,
    settings,
    cipher: SessionCipher,
) -> None:
    while not stop_event.is_set():
        try:
            await poll_gift_market(bot, db, settings, cipher)
        except Exception:
            logging.exception("Gift market worker failed")
        await _sleep_or_stop(stop_event, settings.gift_poll_interval_seconds)


async def _sleep_or_stop(stop_event: asyncio.Event, seconds: int) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        return


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)


if __name__ == "__main__":
    asyncio.run(main())
