from __future__ import annotations

import html
from datetime import timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message, PreCheckoutQuery
from sqlalchemy import select

from app.config import Settings
from app.db import Database
from app.keyboards import buy_menu, crypto_asset_menu, crypto_invoice_menu, main_menu, profile_menu, referral_menu, subscriptions_menu
from app.models import Payment, Subscription, SubscriptionInvite, TelegramUser, utcnow
from app.premium_emoji import tg_emoji
from app.plans import crypto_amount, get_plan, get_plans
from app.services.payments import (
    create_cryptobot_invoice,
    create_payment,
    cryptobot_invoice_paid,
    send_stars_invoice,
)
from app.services.subscriptions import (
    activate_subscription,
    create_private_invite,
    get_active_subscription,
)
from app.services.users import get_or_create_user

router = Router()


@router.message(CommandStart())
async def start(message: Message, db: Database, settings: Settings) -> None:
    ref_code = None
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) == 2:
            ref_code = _extract_ref_code(parts[1])

    async with db.session() as session:
        user = await get_or_create_user(session, message.from_user, ref_code)
        if user.is_blocked:
            await message.answer("Доступ ограничен.")
            return

    await message.answer(
        _home_text(message.from_user),
        reply_markup=main_menu(show_trial=not user.trial_activated),
        parse_mode="HTML",
    )


@router.message(Command("paysupport"))
async def pay_support(message: Message, settings: Settings) -> None:
    if settings.support_username:
        await message.answer(f"По платежам пиши сюда: @{settings.support_username}")
    else:
        await message.answer("По платежам напиши администратору этого бота.")


@router.message(Command("id"))
async def chat_id(message: Message, settings: Settings) -> None:
    if message.chat.type == "private" and not _is_admin(message.from_user.id, settings):
        return
    await message.answer(
        "ID этого чата:\n"
        f"`{message.chat.id}`\n"
        f"Тип: {message.chat.type}",
        parse_mode="Markdown",
    )


@router.callback_query(F.data == "home")
async def home(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    async with db.session() as session:
        user = await get_or_create_user(session, callback.from_user)
    await callback.message.edit_text(
        _home_text(callback.from_user),
        reply_markup=main_menu(show_trial=not user.trial_activated),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    async with db.session() as session:
        user = await get_or_create_user(session, callback.from_user)
        result = await session.execute(
            select(Subscription)
            .where(Subscription.user_id == user.id)
            .order_by(Subscription.expires_at.desc())
            .limit(1)
        )
        sub = result.scalar_one_or_none()
        plan_title = await _subscription_title(session, sub)

    username = f"@{callback.from_user.username}" if callback.from_user.username else "без username"
    if sub:
        expires_at = _as_utc(sub.expires_at)
        is_active = expires_at > utcnow()
        status = "Активна" if is_active else "Истекла"
        status_emoji = "ok" if is_active else "cross"
        expires = expires_at.strftime("%d.%m.%Y %H:%M UTC")
    else:
        status = "Истекла"
        status_emoji = "cross"
        expires = "нет"

    text = (
        f"{tg_emoji('profile')} <b>Мой профиль</b>\n\n"
        f"{tg_emoji('profile')} ID: <code>{callback.from_user.id}</code>\n"
        f"{tg_emoji('link')} Username: <b>{username}</b>\n\n"
        f"{tg_emoji('smile')} Статус: {tg_emoji(status_emoji)} <b>{status}</b>\n"
        f"{tg_emoji('gift')} Тип: <b>{plan_title}</b>\n"
        f"{tg_emoji('calendar')} Истекает: {tg_emoji('time')} <b>{expires}</b>\n\n"
        f"{tg_emoji('chart_up')} Воркнуто логов: <b>{user.claimed_listings_count or 0}</b>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=profile_menu(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "subscriptions")
async def subscriptions(callback: CallbackQuery, db: Database) -> None:
    async with db.session() as session:
        plans = await get_plans(session)
    await callback.message.edit_text(
        f"{tg_emoji('gift')} <b>Выбери срок подписки:</b>",
        reply_markup=subscriptions_menu(plans),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buy:"))
async def buy(callback: CallbackQuery, db: Database) -> None:
    plan_code = callback.data.split(":", maxsplit=1)[1]
    async with db.session() as session:
        plan = await get_plan(session, plan_code)
    await callback.message.edit_text(
        f"{tg_emoji('gift')} <b>{plan.title}</b>\n"
        f"{tg_emoji('money')} Цена: <b>{plan.ton:g} TON</b>, <b>{plan.usdt:g} USDT</b> или <b>{plan.stars} Stars</b>",
        reply_markup=buy_menu(plan_code),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_stars:"))
async def pay_stars(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    plan_code = callback.data.split(":", maxsplit=1)[1]
    async with db.session() as session:
        user = await get_or_create_user(session, callback.from_user)
        payment = await create_payment(session, user, plan_code)

    await send_stars_invoice(bot, callback.from_user.id, payment)
    await callback.answer("Инвойс отправлен")


@router.callback_query(F.data.startswith("crypto_asset:"))
async def choose_crypto_asset(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not settings.cryptobot_token:
        await callback.answer("CryptoBot пока не подключён", show_alert=True)
        return

    plan_code = callback.data.split(":", maxsplit=1)[1]
    async with db.session() as session:
        plan = await get_plan(session, plan_code)
    await callback.message.edit_text(
        f"{tg_emoji('crypto')} <b>Выбери валюту CryptoBot</b>\n\n"
        f"Тариф: <b>{plan.title}</b>",
        reply_markup=crypto_asset_menu(plan_code, plan.ton, plan.usdt),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_crypto:"))
async def pay_crypto(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not settings.cryptobot_token:
        await callback.answer("CryptoBot пока не подключён", show_alert=True)
        return

    _, plan_code, asset = callback.data.split(":", maxsplit=2)
    asset = asset.upper()
    if asset not in {"TON", "USDT"}:
        await callback.answer("Валюта не поддерживается", show_alert=True)
        return
    async with db.session() as session:
        user = await get_or_create_user(session, callback.from_user)
        plan = await get_plan(session, plan_code)
        payment = await create_payment(session, user, plan_code, crypto_asset=asset)
        try:
            invoice_id, pay_url = await create_cryptobot_invoice(settings, payment)
        except Exception as exc:
            payment.status = "failed"
            await callback.answer(f"Не смог создать счёт: {exc}", show_alert=True)
            return
        payment.provider_payment_charge_id = invoice_id
        payment.telegram_payment_charge_id = pay_url

    amount = crypto_amount(plan, asset)
    await callback.message.edit_text(
        f"{tg_emoji('crypto')} <b>Оплата через CryptoBot</b>\n\n"
        f"Тариф: <b>{plan.title}</b>\n"
        f"Сумма: <b>{amount:g} {asset}</b>\n\n"
        "После оплаты нажми <b>Проверить оплату</b>.",
        reply_markup=crypto_invoice_menu(payment.id, pay_url),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("check_crypto:"))
async def check_crypto_payment(
    callback: CallbackQuery,
    bot: Bot,
    db: Database,
    settings: Settings,
) -> None:
    payment_id = int(callback.data.rsplit(":", maxsplit=1)[1])
    async with db.session() as session:
        user = await get_or_create_user(session, callback.from_user)
        payment = await session.get(Payment, payment_id)
        if payment is None or payment.user_id != user.id:
            await callback.answer("Платёж не найден", show_alert=True)
            return
        if payment.status == "paid":
            await callback.answer("Уже оплачено", show_alert=True)
            return
        if not payment.provider_payment_charge_id:
            await callback.answer("У счёта нет invoice id", show_alert=True)
            return

        try:
            paid = await cryptobot_invoice_paid(settings, payment.provider_payment_charge_id)
        except Exception as exc:
            await callback.answer(f"Не смог проверить оплату: {exc}", show_alert=True)
            return

        if not paid:
            await callback.answer("Оплата пока не найдена", show_alert=True)
            return

        payment.status = "paid"
        duration = timedelta(seconds=payment.duration_seconds) if payment.duration_seconds else None
        subscription = await activate_subscription(session, user, payment.plan_code, "cryptobot", duration=duration)
        await _reward_referrer(session, user)

    invite = await create_private_invite(bot, settings)
    expires = subscription.expires_at.astimezone(settings.tzinfo).strftime("%d.%m.%Y %H:%M")
    sent = await callback.message.edit_text(
        f"{tg_emoji('ok')} <b>Оплата принята.</b>\n"
        f"Подписка действует до <b>{expires}</b>.\n"
        f"Ссылка в приватный чат: {invite}",
        reply_markup=main_menu(show_trial=not user.trial_activated),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await _remember_invite(db, user, invite, sent.chat.id, sent.message_id)
    await callback.answer("Оплата подтверждена")


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery, db: Database) -> None:
    async with db.session() as session:
        result = await session.execute(
            select(Payment).where(
                Payment.payload == pre_checkout_query.invoice_payload,
                Payment.status == "created",
            )
        )
        payment = result.scalar_one_or_none()

    await pre_checkout_query.answer(
        ok=payment is not None,
        error_message=None if payment is not None else "Платеж не найден или уже обработан.",
    )


@router.message(F.successful_payment)
async def successful_payment(message: Message, bot: Bot, db: Database, settings: Settings) -> None:
    payload = message.successful_payment.invoice_payload
    async with db.session() as session:
        result = await session.execute(select(Payment).where(Payment.payload == payload))
        payment = result.scalar_one_or_none()
        if payment is None:
            await message.answer("Оплата пришла, но payload не найден. Напиши админу.")
            return

        payment.status = "paid"
        payment.telegram_payment_charge_id = message.successful_payment.telegram_payment_charge_id
        payment.provider_payment_charge_id = message.successful_payment.provider_payment_charge_id

        user = await session.get(TelegramUser, payment.user_id)
        duration = timedelta(seconds=payment.duration_seconds) if payment.duration_seconds else None
        subscription = await activate_subscription(session, user, payment.plan_code, "stars", duration=duration)
        await _reward_referrer(session, user)

    invite = await create_private_invite(bot, settings)
    expires = subscription.expires_at.astimezone(settings.tzinfo).strftime("%d.%m.%Y %H:%M")
    sent = await message.answer(
        "Оплата принята, доступ активирован.\n"
        f"Подписка действует до {expires}.\n"
        f"Ссылка в приватный чат: {invite}",
        reply_markup=main_menu(show_trial=not user.trial_activated),
        disable_web_page_preview=True,
    )
    await _remember_invite(db, user, invite, sent.chat.id, sent.message_id)


@router.callback_query(F.data == "trial")
async def trial(callback: CallbackQuery, bot: Bot, db: Database, settings: Settings) -> None:
    async with db.session() as session:
        user = await get_or_create_user(session, callback.from_user)
        if user.trial_activated:
            await callback.answer("Триал уже активировали", show_alert=True)
            return

        user.trial_activated = True
        subscription = await activate_subscription(
            session,
            user,
            plan_code="trial",
            source="trial",
            duration=timedelta(hours=settings.trial_hours),
        )
        await _reward_referrer(session, user)

    invite = await create_private_invite(bot, settings)
    expires = subscription.expires_at.astimezone(settings.tzinfo).strftime("%d.%m.%Y %H:%M")
    sent = await callback.message.edit_text(
        f"{tg_emoji('party')} <b>Подписка активна.</b>\n"
        f"Доступ действует до <b>{expires}</b>.\n"
        f"Ссылка в приватный чат: {invite}",
        reply_markup=main_menu(show_trial=False),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await _remember_invite(db, user, invite, sent.chat.id, sent.message_id)
    await callback.answer()


@router.callback_query(F.data == "referral")
async def referral(callback: CallbackQuery, bot: Bot, db: Database, settings: Settings) -> None:
    async with db.session() as session:
        user = await get_or_create_user(session, callback.from_user)
        result = await session.execute(
            select(TelegramUser).where(
                TelegramUser.referred_by_id == user.id,
            )
        )
        invited = list(result.scalars().all())
        invited_total = len(invited)
        activated_refs = sum(1 for row in invited if row.referral_rewarded_at is not None)

    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{user.ref_code}"
    earned_hours = activated_refs
    text = (
        f"{tg_emoji('party')} <b>Реферальная программа</b>\n\n"
        f"{tg_emoji('gift')} Приглашай друзей — получай <b>1 ч подписки</b> за каждого, кто активирует триал или оплатит тариф.\n\n"
        f"{tg_emoji('profile')} Приглашено всего: <b>{invited_total}</b>\n"
        f"{tg_emoji('chart_up')} Активировали бота: <b>{activated_refs}</b>\n"
        f"{tg_emoji('time')} Заработано часов: <b>{earned_hours}</b>\n\n"
        f"{tg_emoji('file')} <b>Твоя ссылка:</b>\n"
        f"{link}\n\n"
        f"{tg_emoji('gift')} <i>Бонус начисляется один раз за каждого приглашённого и только когда он реально начнёт пользоваться ботом.</i>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=referral_menu(link),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.chat_member()
async def private_chat_member(event: ChatMemberUpdated, bot: Bot, db: Database, settings: Settings) -> None:
    if event.chat.id != settings.private_chat_id:
        return
    if str(event.new_chat_member.status) not in {"ChatMemberStatus.MEMBER", "member", "ChatMemberStatus.ADMINISTRATOR", "administrator"}:
        return

    async with db.session() as session:
        result = await session.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == event.new_chat_member.user.id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return
        invite_result = await session.execute(
            select(SubscriptionInvite)
            .where(SubscriptionInvite.user_id == user.id, SubscriptionInvite.used_at.is_(None))
            .order_by(SubscriptionInvite.created_at.desc())
            .limit(1)
        )
        invite = invite_result.scalar_one_or_none()
        if invite is None:
            return
        subscription = await get_active_subscription(session, user.id)
        invite.used_at = utcnow()

    text = f"{tg_emoji('ok')} <b>Подписка активна.</b>"
    if subscription:
        expires = subscription.expires_at.astimezone(settings.tzinfo).strftime("%d.%m.%Y %H:%M")
        text += f"\nДоступ действует до <b>{expires}</b>."

    await bot.edit_message_text(
        text,
        chat_id=invite.message_chat_id,
        message_id=invite.message_id,
        reply_markup=main_menu(show_trial=not user.trial_activated),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _remember_invite(db: Database, user: TelegramUser, invite_link: str, chat_id: int, message_id: int) -> None:
    async with db.session() as session:
        session.add(
            SubscriptionInvite(
                user_id=user.id,
                invite_link=invite_link,
                message_chat_id=chat_id,
                message_id=message_id,
            )
        )


async def _reward_referrer(session, user: TelegramUser) -> None:
    if not user.referred_by_id or user.referral_rewarded_at is not None:
        return
    referrer = await session.get(TelegramUser, user.referred_by_id)
    if referrer is None or referrer.id == user.id:
        return
    user.referral_rewarded_at = utcnow()
    await activate_subscription(
        session,
        referrer,
        plan_code="referral",
        source="referral",
        duration=timedelta(hours=1),
    )


def _extract_ref_code(payload: str) -> str | None:
    value = payload.strip()
    if not value:
        return None
    if value.startswith("ref_"):
        return value.removeprefix("ref_")
    if value.startswith("ref"):
        return value.removeprefix("ref")
    return value


async def _subscription_title(session, sub: Subscription | None) -> str:
    if sub is None:
        return "нет"
    if sub.plan_code == "trial":
        return "Пробная (Trial)"
    if sub.plan_code == "referral":
        return "Реферальная"
    try:
        plan = await get_plan(session, sub.plan_code)
    except KeyError:
        return sub.plan_code
    return plan.title


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _home_text(user) -> str:
    name = html.escape(user.full_name or user.first_name or "друг", quote=False)
    return (
        f"{tg_emoji('bot')} <b>Добро пожаловать, {name}!</b>\n\n"
        "Этот бот трекает ресейл NFT-подарков и присылает горячие лоты "
        "первым подписчикам.\n\n"
        f"{tg_emoji('down')} Жми кнопки ниже:"
    )


def _is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids
