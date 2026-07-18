from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotSetting


async def get_setting(session: AsyncSession, key: str, default: str | None = None) -> str | None:
    result = await session.execute(select(BotSetting).where(BotSetting.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else default


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    result = await session.execute(select(BotSetting).where(BotSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting is None:
        session.add(BotSetting(key=key, value=value))
    else:
        setting.value = value


async def monitor_enabled(session: AsyncSession) -> bool:
    return (await get_setting(session, "monitor_enabled", "1")) == "1"


async def toggle_monitor(session: AsyncSession) -> bool:
    enabled = not await monitor_enabled(session)
    await set_setting(session, "monitor_enabled", "1" if enabled else "0")
    return enabled
