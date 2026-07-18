from __future__ import annotations

import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.keyboards import expired_subscription_menu
from app.models import Subscription, TelegramUser, utcnow
from app.premium_emoji import tg_emoji
from app.plans import get_plan

logger = logging.getLogger(__name__)


async def get_active_subscription(
    session: AsyncSession,
    user_id: int,
    now: datetime | None = None,
) -> Subscription | None:
    now = now or utcnow()
    result = await session.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.expires_at > now)
        .order_by(Subscription.expires_at.desc())
        .limit(1)
    )
    subscription = result.scalar_one_or_none()
    if subscription is not None:
        subscription.expires_at = _as_utc(subscription.expires_at)
    return subscription


async def activate_subscription(
    session: AsyncSession,
    user: TelegramUser,
    plan_code: str,
    source: str,
    duration: timedelta | None = None,
) -> Subscription:
    now = utcnow()
    active = await get_active_subscription(session, user.id, now)
    active_until = _as_utc(active.expires_at) if active else None
    start = active_until if active_until and active_until > now else now
    plan_duration = duration or (await get_plan(session, plan_code)).duration
    subscription = Subscription(
        user_id=user.id,
        plan_code=plan_code,
        source=source,
        started_at=start,
        expires_at=start + plan_duration,
    )
    session.add(subscription)
    await session.flush()
    return subscription


async def create_private_invite(bot: Bot, settings: Settings) -> str:
    expire_date = datetime.now(timezone.utc) + timedelta(minutes=settings.invite_ttl_minutes)
    invite = await bot.create_chat_invite_link(
        chat_id=settings.private_chat_id,
        name="subscription-access",
        expire_date=expire_date,
        member_limit=1,
        creates_join_request=False,
    )
    return invite.invite_link


async def revoke_expired_access(bot: Bot, db, settings: Settings) -> None:
    now = utcnow()
    async with db.session() as session:
        result = await session.execute(
            select(Subscription, TelegramUser)
            .join(TelegramUser, TelegramUser.id == Subscription.user_id)
            .where(
                Subscription.expires_at <= now,
                Subscription.access_revoked_at.is_(None),
            )
            .limit(100)
        )
        rows = result.all()

        for expired, user in rows:
            if user.telegram_id in settings.admin_ids:
                expired.access_revoked_at = now
                continue

            active = await get_active_subscription(session, user.id, now)
            if active:
                expired.access_revoked_at = now
                continue

            try:
                await bot.ban_chat_member(settings.private_chat_id, user.telegram_id)
                await bot.unban_chat_member(settings.private_chat_id, user.telegram_id)
                expired.access_revoked_at = now
                with suppress(Exception):
                    await bot.send_message(
                        user.telegram_id,
                        f"{tg_emoji('lock')} <b>Подписка истекла</b>\n\n"
                        "Ты был удалён из канала. Чтобы вернуться — продли подписку:",
                        parse_mode="HTML",
                        reply_markup=expired_subscription_menu(),
                    )
                logger.info("Revoked private chat access for %s", user.telegram_id)
            except Exception as exc:
                logger.warning("Cannot revoke access for %s: %s", user.telegram_id, exc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
