from __future__ import annotations

from aiogram.types import User as AiogramUser
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TelegramUser


def make_ref_code(telegram_id: int) -> str:
    return f"u{telegram_id:x}"


async def make_unique_ref_code(session: AsyncSession, telegram_id: int) -> str:
    base = make_ref_code(telegram_id)
    code = base
    index = 1
    while True:
        result = await session.execute(select(TelegramUser.id).where(TelegramUser.ref_code == code))
        if result.scalar_one_or_none() is None:
            return code
        index += 1
        code = f"{base}_{index}"


async def get_or_create_user(
    session: AsyncSession,
    tg_user: AiogramUser,
    start_ref_code: str | None = None,
) -> TelegramUser:
    result = await session.execute(
        select(TelegramUser).where(TelegramUser.telegram_id == tg_user.id)
    )
    user = result.scalar_one_or_none()

    referrer: TelegramUser | None = None
    if start_ref_code:
        ref_conditions = [TelegramUser.ref_code == start_ref_code]
        if start_ref_code.isdigit():
            ref_conditions.append(TelegramUser.telegram_id == int(start_ref_code))
        ref_result = await session.execute(
            select(TelegramUser).where(or_(*ref_conditions))
        )
        referrer = ref_result.scalar_one_or_none()

    if user is None:
        user = TelegramUser(
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            is_premium=bool(tg_user.is_premium),
            ref_code=await make_unique_ref_code(session, tg_user.id),
            referred_by_id=referrer.id if referrer and referrer.telegram_id != tg_user.id else None,
        )
        session.add(user)
        await session.flush()
        return user

    user.username = tg_user.username
    user.first_name = tg_user.first_name
    user.last_name = tg_user.last_name
    user.is_premium = bool(tg_user.is_premium)
    if referrer and referrer.telegram_id != tg_user.id and user.referred_by_id is None:
        user.referred_by_id = referrer.id
    await session.flush()
    return user
