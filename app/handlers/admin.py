from __future__ import annotations

import asyncio
import html
import re
import time
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, or_, select
from telethon.errors import PasswordHashInvalidError, PhoneCodeExpiredError, PhoneCodeInvalidError

from app.config import Settings, get_settings
from app.db import Database
from app.keyboards import (
    admin_back_menu,
    admin_menu,
    admin_plan_menu,
    admin_plans_menu,
    admin_stats_menu,
    admin_user_menu,
    admin_users_menu,
    code_keypad_menu,
    confirm_broadcast_menu,
    level_menu,
    max_price_menu,
    messages_filter_menu,
    session_item_menu,
    sessions_list_menu,
    sessions_menu,
)
from app.models import AccountSession, GiftListing, Payment, Subscription, TelegramUser, utcnow
from app.premium_emoji import emoji_id, tg_emoji
from app.plans import get_plan, get_plans, parse_duration, set_plan_field
from app.security import SessionCipher
from app.services.listings import fake_listing, listing_keyboard, poll_gift_market
from app.services.settings import get_setting, monitor_enabled, set_setting, toggle_monitor
from app.services.subscriptions import activate_subscription, get_active_subscription
from app.services.telegram_login import request_login_code, sign_in_with_code, sign_in_with_password

router = Router()
_claim_cooldowns: dict[int, float] = {}
_claimed_originals: dict[str, str] = {}


class AddSession(StatesGroup):
    name = State()
    value = State()


class PhoneSession(StatesGroup):
    name = State()
    phone = State()
    code = State()
    password = State()


class Broadcast(StatesGroup):
    text = State()
    confirm = State()


class PriceFilter(StatesGroup):
    max_price = State()


class PlanEdit(StatesGroup):
    value = State()


class UserLookup(StatesGroup):
    query = State()
    message = State()
    subscription = State()


@router.message(Command("admin"))
async def admin_command(message: Message, db: Database, settings: Settings, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    await state.clear()
    await _answer_admin_panel(message, db)


@router.callback_query(F.data == "admin")
async def admin_callback(callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await _edit_admin_panel(callback.message, db)
    await callback.answer()


@router.callback_query(F.data == "admin:stats")
async def stats(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return

    async with db.session() as session:
        users = await _count(session, TelegramUser.id)
        paid = await _count(session, Payment.id, Payment.status == "paid")
        listings = await _count(session, GiftListing.id)
        sessions_total = await _count(session, AccountSession.id)
        sessions_active = await _count(session, AccountSession.id, AccountSession.is_active.is_(True))
        active_subs = await _count(session, Subscription.id, Subscription.expires_at > utcnow())
        last_fetched = await get_setting(session, "last_poll_fetched", "0")
        last_sent = await get_setting(session, "last_poll_sent", "0")
        last_dup = await get_setting(session, "last_poll_skipped_duplicates", "0")
        last_ru = await get_setting(session, "last_poll_skipped_ru", "0")
        last_error = await get_setting(session, "last_poll_error", "")
        last_at = await get_setting(session, "last_poll_at", "еще не было")

    text = (
        "Статистика\n"
        f"Пользователей: {users}\n"
        f"Активных подписок: {active_subs}\n"
        f"Оплаченных платежей: {paid}\n"
        f"Сессий: {sessions_active}/{sessions_total} активных\n"
        f"Листингов в базе: {listings}\n\n"
        "Последняя проверка:\n"
        f"Время: {last_at}\n"
        f"Получено: {last_fetched}\n"
        f"Отправлено: {last_sent}\n"
        f"Дубли: {last_dup}\n"
        f"Отсечено фильтрами: {last_ru}\n"
        f"Ошибка: {last_error or 'нет'}"
    )
    await callback.message.edit_text(text, reply_markup=await _admin_markup(db))
    await callback.answer()


@router.callback_query(F.data == "admin:users")
async def users_stats(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    async with db.session() as session:
        users = await _count(session, TelegramUser.id)
        active_subs = await _count(session, Subscription.id, Subscription.expires_at > utcnow())
        paid = await _count(session, Payment.id, Payment.status == "paid")
    await callback.message.edit_text(
        "Пользователи\n"
        f"Всего: {users}\n"
        f"Активных подписок: {active_subs}\n"
        f"Платежей: {paid}",
        reply_markup=admin_stats_menu(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:level")
async def level_settings(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    async with db.session() as session:
        current = int(await get_setting(session, "filter_max_level", "2") or "2")
        mode = await get_setting(session, "filter_level_mode", "max") or "max"
    await callback.message.edit_text("Выбери фильтр Level:", reply_markup=level_menu(current, mode))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:set_level:"))
async def set_level(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    value = callback.data.rsplit(":", maxsplit=1)[1]
    async with db.session() as session:
        if value == "gt5":
            await set_setting(session, "filter_level_mode", "gt5")
            await set_setting(session, "filter_max_level", "5")
            label = "> 5"
        else:
            level = int(value)
            await set_setting(session, "filter_level_mode", "max")
            await set_setting(session, "filter_max_level", str(level))
            label = f"≤ {level}"
    await callback.message.edit_text(
        await _admin_text(db, prefix=f"Level-фильтр обновлен: {label}."),
        reply_markup=await _admin_markup(db),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:max_price")
async def max_price_settings(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    async with db.session() as session:
        current = int(await get_setting(session, "filter_max_price_stars", "0") or "0")
    await callback.message.edit_text(
        "Выбери максимальную цену подарка в Stars.\n"
        "Лоты дороже лимита не будут отправляться.",
        reply_markup=max_price_menu(current),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:set_max_price:"))
async def set_max_price(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    price = int(callback.data.rsplit(":", maxsplit=1)[1])
    async with db.session() as session:
        await set_setting(session, "filter_max_price_stars", str(price))
    await callback.message.edit_text(
        await _admin_text(db, prefix=f"Фильтр цены обновлён: {_price_label(price)}."),
        reply_markup=await _admin_markup(db),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:max_price:custom")
async def custom_max_price(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(PriceFilter.max_price)
    await callback.message.edit_text(
        "Пришли максимальную цену числом в Stars.\n"
        "Например: 5000\n"
        "Чтобы отключить лимит, пришли 0.",
        reply_markup=admin_back_menu("admin:max_price"),
    )
    await callback.answer()


@router.message(PriceFilter.max_price)
async def custom_max_price_value(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    if not message.text:
        await message.answer("Нужно число в Stars.")
        return

    raw = message.text.strip().replace(" ", "").replace("_", "")
    if not raw.isdigit():
        await message.answer("Пришли только число, например 5000. Для отключения лимита - 0.")
        return

    price = int(raw)
    if price > 10_000_000:
        await message.answer("Слишком большой лимит. Поставь число до 10000000 Stars.")
        return

    async with db.session() as session:
        await set_setting(session, "filter_max_price_stars", str(price))

    await state.clear()
    await message.answer(
        await _admin_text(db, prefix=f"Фильтр цены обновлён: {_price_label(price)}."),
        reply_markup=await _admin_markup(db),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:messages")
async def messages_settings(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    async with db.session() as session:
        current = await get_setting(session, "filter_messages_mode", "free")
    await callback.message.edit_text(
        "Выбери фильтр сообщений:",
        reply_markup=messages_filter_menu(current or "free"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:set_messages:"))
async def set_messages(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    mode = callback.data.rsplit(":", maxsplit=1)[1]
    labels = {
        "free": "без звёзд",
        "paid_allowed": "можно со звёздами",
        "paid_only": "только звёзды",
    }
    async with db.session() as session:
        await set_setting(session, "filter_messages_mode", mode)
    await callback.message.edit_text(
        await _admin_text(db, prefix=f"Фильтр сообщений: {labels.get(mode, mode)}."),
        reply_markup=await _admin_markup(db),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:plans")
async def admin_plans(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    async with db.session() as session:
        plans = await get_plans(session)
    await callback.message.edit_text(
        f"{tg_emoji('money')} <b>Тарифы</b>\n\nВыбери тариф, который нужно поменять.",
        reply_markup=admin_plans_menu(plans),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:plan:"))
async def admin_plan(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    code = callback.data.rsplit(":", maxsplit=1)[1]
    async with db.session() as session:
        plan = await get_plan(session, code)
    await callback.message.edit_text(_plan_text(plan), reply_markup=admin_plan_menu(plan), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:plan_edit:"))
async def admin_plan_edit(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, code, field = callback.data.split(":", maxsplit=3)
    await state.set_state(PlanEdit.value)
    await state.update_data(plan_code=code, plan_field=field)
    await callback.message.edit_text(_plan_edit_prompt(field), reply_markup=admin_back_menu(f"admin:plan:{code}"))
    await callback.answer()


@router.message(PlanEdit.value)
async def admin_plan_edit_value(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    if not message.text:
        await message.answer("Пришли значение текстом.")
        return

    data = await state.get_data()
    code = data["plan_code"]
    field = data["plan_field"]
    try:
        value, label = _normalize_plan_value(field, message.text)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    async with db.session() as session:
        await set_plan_field(session, code, field, value)
        plan = await get_plan(session, code)

    await state.clear()
    await message.answer(
        _plan_text(plan, prefix=f"Обновлено: {label}."),
        reply_markup=admin_plan_menu(plan),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:sessions")
async def sessions(callback: CallbackQuery, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("Управление сессиями", reply_markup=sessions_menu())
    await callback.answer()


@router.callback_query(F.data == "admin:add_session")
@router.callback_query(F.data == "admin:add_session_phone")
async def add_session(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(PhoneSession.name)
    await callback.message.edit_text(
        "Напиши название сессии, например main-account.\n"
        "Дальше попрошу телефон, код Telegram и 2FA-пароль, если он включен.",
        reply_markup=admin_back_menu("admin:sessions"),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:add_session_string")
async def add_session_string(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AddSession.name)
    await callback.message.edit_text("Напиши название сессии, например main-account.", reply_markup=admin_back_menu("admin:sessions"))
    await callback.answer()


@router.message(PhoneSession.name)
async def phone_session_name(message: Message, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    if not message.text:
        await message.answer("Нужно текстовое название.")
        return
    await state.update_data(name=message.text.strip())
    await state.set_state(PhoneSession.phone)
    await message.answer("Теперь пришли номер телефона в международном формате, например +79990000000.")


@router.message(PhoneSession.phone)
async def phone_session_phone(message: Message, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    if not message.text:
        await message.answer("Нужен номер телефона текстом.")
        return

    phone = message.text.strip().replace(" ", "")
    await _delete_sensitive(message)
    status = await message.answer("Отправляю код в Telegram...")

    try:
        code_request = await request_login_code(settings, phone)
    except Exception as exc:
        await state.clear()
        await status.edit_text(f"Не смог отправить код: {exc}")
        return

    await state.update_data(
        phone=phone,
        temp_session=code_request.temp_session,
        phone_code_hash=code_request.phone_code_hash,
        code_digits="",
    )
    await state.set_state(PhoneSession.code)
    await status.edit_text(
        "Код отправлен.\n"
        "Введи код кнопками ниже.",
        reply_markup=code_keypad_menu(),
    )


@router.callback_query(PhoneSession.code, F.data.startswith("login_code:"))
async def phone_session_code_button(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    cipher: SessionCipher,
    settings: Settings,
) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return

    action = callback.data.split(":")
    data = await state.get_data()
    digits = data.get("code_digits", "")

    if action[1] == "digit":
        if len(digits) >= 8:
            await callback.answer("Слишком длинный код", show_alert=True)
            return
        digits += action[2]
        await state.update_data(code_digits=digits)
        await _render_code_keypad(callback, digits)
        await callback.answer()
        return

    if action[1] == "backspace":
        digits = digits[:-1]
        await state.update_data(code_digits=digits)
        await _render_code_keypad(callback, digits)
        await callback.answer()
        return

    if action[1] == "clear":
        await state.update_data(code_digits="")
        await _render_code_keypad(callback, "")
        await callback.answer("Очищено")
        return

    if action[1] == "submit":
        if len(digits) < 3:
            await callback.answer("Введи код", show_alert=True)
            return
        await callback.answer()
        await _finish_phone_code(callback, state, db, cipher, settings, digits)


@router.message(PhoneSession.code)
async def phone_session_code(
    message: Message,
    state: FSMContext,
    db: Database,
    cipher: SessionCipher,
    settings: Settings,
) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    if not message.text:
        await message.answer("Нужен код текстом.")
        return

    code = message.text.strip().replace(" ", "").replace("-", "")
    await _delete_sensitive(message)
    await _finish_phone_code(message, state, db, cipher, settings, code)


@router.message(PhoneSession.password)
async def phone_session_password(
    message: Message,
    state: FSMContext,
    db: Database,
    cipher: SessionCipher,
    settings: Settings,
) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    if not message.text:
        await message.answer("Нужен 2FA-пароль текстом.")
        return

    password = message.text.strip()
    await _delete_sensitive(message)
    data = await state.get_data()

    try:
        session_string = await sign_in_with_password(settings, data["temp_session"], password)
    except PasswordHashInvalidError:
        await message.answer("Пароль неверный. Пришли 2FA-пароль еще раз.")
        return
    except Exception as exc:
        await state.clear()
        await message.answer(f"Не смог войти по 2FA: {exc}", reply_markup=admin_menu())
        return

    await _save_account_session(db, cipher, data["name"], session_string)
    await state.clear()
    await message.answer("Сессия создана, зашифрована и включена.", reply_markup=admin_menu())


@router.message(AddSession.name)
async def add_session_name(message: Message, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    if not message.text:
        await message.answer("Нужно текстовое название.")
        return
    await state.update_data(name=message.text.strip())
    await state.set_state(AddSession.value)
    await message.answer("Теперь пришли Telethon StringSession одной строкой.")


@router.message(AddSession.value)
async def add_session_value(
    message: Message,
    state: FSMContext,
    db: Database,
    cipher: SessionCipher,
    settings: Settings,
) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    if not message.text:
        await message.answer("Нужна StringSession текстом.")
        return
    data = await state.get_data()
    session_string = message.text.strip()

    await _save_account_session(db, cipher, data["name"], session_string)

    await state.clear()
    await _delete_sensitive(message)
    await message.answer("Сессия добавлена и включена.", reply_markup=admin_menu())


@router.callback_query(F.data == "admin:list_sessions")
async def list_sessions(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    async with db.session() as session:
        result = await session.execute(select(AccountSession).order_by(AccountSession.id.desc()))
        rows = list(result.scalars().all())

    if not rows:
        await callback.message.edit_text("Сессий пока нет.", reply_markup=sessions_menu())
    else:
        await callback.message.edit_text("Сессии:", reply_markup=sessions_list_menu(rows))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:session:view:"))
async def view_session(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    session_id = int(callback.data.rsplit(":", maxsplit=1)[1])
    async with db.session() as session:
        item = await session.get(AccountSession, session_id)

    if item is None:
        await callback.answer("Сессия не найдена", show_alert=True)
        return

    text = (
        f"Сессия #{item.id}\n"
        f"Название: {item.name}\n"
        f"Статус: {'активна' if item.is_active else 'выключена'}\n"
        f"Последняя ошибка: {item.last_error or 'нет'}"
    )
    await callback.message.edit_text(text, reply_markup=session_item_menu(item.id, item.is_active))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:session:enable:"))
@router.callback_query(F.data.startswith("admin:session:disable:"))
@router.callback_query(F.data.startswith("admin:session:delete:"))
async def mutate_session(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return

    _, _, action, session_id_raw = callback.data.split(":")
    session_id = int(session_id_raw)
    async with db.session() as session:
        item = await session.get(AccountSession, session_id)
        if item is None:
            await callback.answer("Сессия не найдена", show_alert=True)
            return
        if action == "enable":
            item.is_active = True
        elif action == "disable":
            item.is_active = False
        elif action == "delete":
            await session.delete(item)

    await callback.message.edit_text("Готово.", reply_markup=sessions_menu())
    await callback.answer()


@router.callback_query(F.data == "admin:toggle_monitor")
async def monitor_toggle(
    callback: CallbackQuery,
    bot: Bot,
    db: Database,
    cipher: SessionCipher,
    settings: Settings,
) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return

    async with db.session() as session:
        enabled = await toggle_monitor(session)

    if enabled:
        await callback.answer("Парсер запущен, проверяю рынок...")
        await callback.message.edit_text(
            await _admin_text(db),
            reply_markup=await _admin_markup(db),
            parse_mode="HTML",
        )
        await poll_gift_market(bot, db, settings, cipher, force=True)
        return

    await callback.message.edit_text(
        await _admin_text(db),
        reply_markup=await _admin_markup(db),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:check_now")
async def check_now(
    callback: CallbackQuery,
    bot: Bot,
    db: Database,
    cipher: SessionCipher,
    settings: Settings,
) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer("Проверяю рынок...")
    await callback.message.edit_text("Проверяю рынок и RU-фильтр...")
    await poll_gift_market(bot, db, settings, cipher, force=True)
    await callback.message.edit_text(
        await _admin_text(db, prefix="Проверка завершена."),
        reply_markup=await _admin_markup(db),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:test_listing")
async def test_listing(callback: CallbackQuery, bot: Bot, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    listing = fake_listing(settings)
    await bot.send_message(
        settings.listings_chat_id,
        listing.text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=listing_keyboard(listing.data),
    )
    await callback.answer("Тест отправлен")


@router.callback_query(F.data.startswith("listing_work:"))
@router.callback_query(F.data.startswith("listing_claim:"))
async def listing_claim(callback: CallbackQuery, bot: Bot, db: Database, settings: Settings) -> None:
    if callback.message.chat.id != settings.listings_chat_id:
        await callback.answer("Не тот чат", show_alert=True)
        return

    listing_key = callback.data.split(":", maxsplit=1)[1]
    worker = _worker_label(callback.from_user)
    current_text = callback.message.html_text or callback.message.text or ""
    if "Взято:" in current_text or "Занято:" in current_text or "Воркнут(а) воркером:" in current_text:
        await callback.answer("Уже в работе", show_alert=True)
        return

    async with db.session() as session:
        result = await session.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == callback.from_user.id)
        )
        user = result.scalar_one_or_none()
        active = await get_active_subscription(session, user.id) if user else None

    if active is None:
        now = time.monotonic()
        last = _claim_cooldowns.get(callback.from_user.id, 0)
        wait = 5 - (now - last)
        if wait > 0:
            await callback.answer(f"Подожди {int(wait) + 1} сек.", show_alert=True)
            return
        _claim_cooldowns[callback.from_user.id] = now

    try:
        await bot.send_message(
            callback.from_user.id,
            current_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=_open_listing_markup(callback.message.reply_markup, listing_key, current_text),
        )
    except Exception:
        await callback.answer("Сначала открой бота в личке.", show_alert=True)
        return

    if user:
        async with db.session() as session:
            stored_user = await session.get(TelegramUser, user.id)
            if stored_user:
                stored_user.claimed_listings_count = (stored_user.claimed_listings_count or 0) + 1

    _claimed_originals[listing_key] = current_text
    await callback.message.edit_text(
        f"{tg_emoji('lock')} <b>Данный лог занят</b>\n\nВзято: {worker}",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=_claimed_listing_markup(callback.message.reply_markup, listing_key, callback.from_user.id),
    )
    await callback.answer("Взято")


@router.callback_query(F.data.startswith("listing_release:"))
async def listing_release(callback: CallbackQuery, settings: Settings) -> None:
    if callback.message.chat.id != settings.listings_chat_id:
        await callback.answer("Не тот чат", show_alert=True)
        return

    _, listing_key, owner_id_raw = callback.data.split(":", maxsplit=2)
    owner_id = int(owner_id_raw)
    if callback.from_user.id != owner_id and not _is_admin(callback.from_user.id, settings):
        await callback.answer("Отменить может только тот, кто занял", show_alert=True)
        return

    current_text = callback.message.html_text or callback.message.text or ""
    clean_text = _claimed_originals.pop(listing_key, None) or _remove_claim_line(current_text)
    await callback.message.edit_text(
        clean_text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=_open_listing_markup(callback.message.reply_markup, listing_key, clean_text),
    )
    await callback.answer("Отменено")


@router.callback_query(F.data == "admin:broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(Broadcast.text)
    await callback.message.edit_text("Пришли текст рассылки.", reply_markup=admin_back_menu("admin"))
    await callback.answer()


@router.message(Broadcast.text)
async def broadcast_text(message: Message, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    if not message.text:
        await message.answer("Нужен текст рассылки.")
        return
    await state.update_data(text=message.text)
    await state.set_state(Broadcast.confirm)
    await message.answer(
        "Отправить эту рассылку всем пользователям?\n\n" + message.text,
        reply_markup=confirm_broadcast_menu(),
    )


@router.callback_query(Broadcast.confirm, F.data == "admin:broadcast:confirm")
async def broadcast_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    db: Database,
    settings: Settings,
) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    text = data["text"]
    await state.clear()

    async with db.session() as session:
        result = await session.execute(select(TelegramUser.telegram_id))
        user_ids = list(result.scalars().all())

    sent = 0
    failed = 0
    for user_id in user_ids:
        try:
            await bot.send_message(user_id, text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await callback.message.edit_text(
        f"Рассылка завершена.\nОтправлено: {sent}\nОшибок: {failed}",
        reply_markup=admin_users_menu(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:user_search")
async def user_search_start(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(UserLookup.query)
    await callback.message.edit_text(
        "Пришли username или Telegram ID пользователя.",
        reply_markup=admin_back_menu("admin:users"),
    )
    await callback.answer()


@router.message(UserLookup.query)
async def user_search_value(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    user = await _find_user(db, message.text or "")
    await state.clear()
    if user is None:
        await message.answer("Пользователь не найден.", reply_markup=admin_users_menu())
        return
    await message.answer(_admin_user_text(user), reply_markup=admin_user_menu(user), parse_mode="HTML")


@router.callback_query(F.data.startswith("admin:user_toggle_block:"))
async def user_toggle_block(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = int(callback.data.rsplit(":", maxsplit=1)[1])
    async with db.session() as session:
        user = await session.get(TelegramUser, user_id)
        if user is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        user.is_blocked = not user.is_blocked
    await callback.message.edit_text(_admin_user_text(user), reply_markup=admin_user_menu(user), parse_mode="HTML")
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("admin:user:"))
async def user_view(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = int(callback.data.rsplit(":", maxsplit=1)[1])
    async with db.session() as session:
        user = await session.get(TelegramUser, user_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await callback.message.edit_text(_admin_user_text(user), reply_markup=admin_user_menu(user), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:user_message:"))
async def user_message_start(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = int(callback.data.rsplit(":", maxsplit=1)[1])
    await state.set_state(UserLookup.message)
    await state.update_data(target_user_id=user_id)
    await callback.message.edit_text("Пришли сообщение для пользователя.", reply_markup=admin_back_menu(f"admin:user:{user_id}"))
    await callback.answer()


@router.message(UserLookup.message)
async def user_message_send(message: Message, state: FSMContext, bot: Bot, db: Database, settings: Settings) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    if not message.text:
        await message.answer("Нужен текст сообщения.")
        return
    data = await state.get_data()
    user_id = int(data["target_user_id"])
    async with db.session() as session:
        user = await session.get(TelegramUser, user_id)
    await state.clear()
    if user is None:
        await message.answer("Пользователь не найден.", reply_markup=admin_users_menu())
        return
    try:
        await bot.send_message(user.telegram_id, message.text)
        await message.answer("Сообщение отправлено.", reply_markup=admin_user_menu(user))
    except Exception as exc:
        await message.answer(f"Не смог отправить: {exc}", reply_markup=admin_user_menu(user))


@router.callback_query(F.data.startswith("admin:user_sub:"))
async def user_sub_start(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = int(callback.data.rsplit(":", maxsplit=1)[1])
    await state.set_state(UserLookup.subscription)
    await state.update_data(target_user_id=user_id)
    await callback.message.edit_text(
        "Пришли срок подписки: 6h, 1d, 7d, 30 дней.",
        reply_markup=admin_back_menu(f"admin:user:{user_id}"),
    )
    await callback.answer()


@router.message(UserLookup.subscription)
async def user_sub_grant(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    try:
        duration = parse_duration(message.text or "")
    except ValueError:
        await message.answer("Не понял срок. Пример: 1d или 30 дней.")
        return
    data = await state.get_data()
    user_id = int(data["target_user_id"])
    async with db.session() as session:
        user = await session.get(TelegramUser, user_id)
        if user is None:
            await state.clear()
            await message.answer("Пользователь не найден.", reply_markup=admin_users_menu())
            return
        sub = await activate_subscription(session, user, "manual", "admin", duration=duration)
    await state.clear()
    expires = sub.expires_at.astimezone(settings.tzinfo).strftime("%d.%m.%Y %H:%M")
    await message.answer(f"Подписка выдана до {expires}.", reply_markup=admin_user_menu(user))


@router.callback_query(Broadcast.confirm, F.data == "admin:broadcast:cancel")
async def broadcast_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("Рассылка отменена.", reply_markup=await _admin_markup(db))
    await callback.answer()


async def _count(session, column, *where) -> int:
    query = select(func.count(column))
    for condition in where:
        query = query.where(condition)
    result = await session.execute(query)
    return int(result.scalar_one())


async def _answer_admin_panel(message: Message, db: Database) -> None:
    text = await _admin_text(db)
    markup = await _admin_markup(db)
    try:
        await message.answer(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as exc:
        if _needs_plain_fallback(exc):
            await message.answer(_without_tg_emoji(text), reply_markup=_without_button_icons(markup))
            return
        raise


async def _edit_admin_panel(message: Message, db: Database) -> None:
    text = await _admin_text(db)
    markup = await _admin_markup(db)
    try:
        await message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as exc:
        if _is_not_modified(exc):
            return
        if _needs_plain_fallback(exc):
            await message.edit_text(_without_tg_emoji(text), reply_markup=_without_button_icons(markup))
            return
        raise


def _is_not_modified(exc: TelegramBadRequest) -> bool:
    return "message is not modified" in str(exc).lower()


def _needs_plain_fallback(exc: TelegramBadRequest) -> bool:
    text = str(exc).lower()
    return "can't parse entities" in text or "can't parse inline keyboard button" in text


def _without_tg_emoji(text: str) -> str:
    return re.sub(r'<tg-emoji emoji-id="[^"]+">([^<]*)</tg-emoji>', r'\1', text)


def _without_button_icons(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_plain_button(button) for button in row]
            for row in markup.inline_keyboard
        ]
    )


def _plain_button(button: InlineKeyboardButton) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=button.text,
        url=button.url,
        callback_data=button.callback_data,
        web_app=button.web_app,
        login_url=button.login_url,
        switch_inline_query=button.switch_inline_query,
        switch_inline_query_current_chat=button.switch_inline_query_current_chat,
        switch_inline_query_chosen_chat=button.switch_inline_query_chosen_chat,
        callback_game=button.callback_game,
        pay=button.pay,
        copy_text=button.copy_text,
    )


async def _admin_text(db: Database, prefix: str | None = None) -> str:
    lines = []
    if prefix:
        lines.extend([prefix, ""])
    lines.append(f"{tg_emoji('settings')} <b>Админ-панель</b>")
    return "\n".join(lines)


async def _admin_markup(db: Database):
    async with db.session() as session:
        enabled = await monitor_enabled(session)
        level = int(await get_setting(session, "filter_max_level", "2") or "2")
        level_mode = await get_setting(session, "filter_level_mode", "max") or "max"
        max_price = int(await get_setting(session, "filter_max_price_stars", "0") or "0")
        messages_mode = await get_setting(session, "filter_messages_mode", "free") or "free"
    messages_label = {
        "free": "без звёзд",
        "paid_allowed": "со звёздами",
        "paid_only": "только звёзды",
    }.get(messages_mode, messages_mode)
    return admin_menu(enabled, level if level_mode == "max" else -1, messages_label, _price_label(max_price))


async def _render_code_keypad(callback: CallbackQuery, digits: str) -> None:
    await callback.message.edit_text(
        "Код отправлен.\n"
        f"Введено цифр: {len(digits)}\n"
        "Нажми OK, когда код введен полностью.",
        reply_markup=code_keypad_menu(),
    )


async def _finish_phone_code(
    event: CallbackQuery | Message,
    state: FSMContext,
    db: Database,
    cipher: SessionCipher,
    settings: Settings,
    code: str,
) -> None:
    data = await state.get_data()
    target = event.message if isinstance(event, CallbackQuery) else event

    if isinstance(event, CallbackQuery):
        await event.message.edit_text("Проверяю код...")
    else:
        await target.answer("Проверяю код...")

    try:
        result = await sign_in_with_code(
            settings=settings,
            temp_session=data["temp_session"],
            phone=data["phone"],
            code=code,
            phone_code_hash=data["phone_code_hash"],
        )
    except PhoneCodeInvalidError:
        await state.update_data(code_digits="")
        await target.answer("Код неверный. Введи его еще раз кнопками.", reply_markup=code_keypad_menu())
        return
    except PhoneCodeExpiredError:
        await state.clear()
        await target.answer("Код истек. Запусти добавление сессии заново.", reply_markup=admin_menu())
        return
    except Exception as exc:
        await state.clear()
        await target.answer(f"Не смог войти по коду: {exc}", reply_markup=admin_menu())
        return

    if result.needs_password:
        await state.update_data(temp_session=result.session_string, code_digits="")
        await state.set_state(PhoneSession.password)
        await target.answer("На аккаунте включен 2FA. Пришли пароль одним сообщением.")
        return

    await _save_account_session(db, cipher, data["name"], result.session_string)
    await state.clear()
    await target.answer("Сессия создана, зашифрована и включена.", reply_markup=admin_menu())


async def _save_account_session(
    db: Database,
    cipher: SessionCipher,
    name: str,
    session_string: str,
) -> None:
    async with db.session() as session:
        session.add(
            AccountSession(
                name=name,
                session_ciphertext=cipher.encrypt(session_string),
                is_active=True,
            )
        )


async def _delete_sensitive(message: Message) -> None:
    with suppress(Exception):
        await message.delete()


def _worker_label(user) -> str:
    if user.username:
        return f"@{html.escape(user.username, quote=False)}"
    name = html.escape(user.full_name or str(user.id), quote=False)
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def _claimed_listing_markup(
    markup: InlineKeyboardMarkup | None,
    listing_key: str,
    user_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отменить",
                    callback_data=f"listing_release:{listing_key}:{user_id}",
                    icon_custom_emoji_id=emoji_id("cross"),
                )
            ]
        ]
    )


def _open_listing_markup(
    markup: InlineKeyboardMarkup | None,
    listing_key: str,
    text: str | None = None,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Занять",
                callback_data=f"listing_claim:{listing_key}",
                icon_custom_emoji_id=emoji_id("ok"),
            )
        ]
    ]
    rows.extend(_url_rows(markup))
    if len(rows) == 1 and text:
        rows.extend(_url_rows_from_text(text))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _url_rows(markup: InlineKeyboardMarkup | None) -> list[list[InlineKeyboardButton]]:
    if not markup:
        return []
    rows: list[list[InlineKeyboardButton]] = []
    for row in markup.inline_keyboard[1:]:
        url_row = [button for button in row if button.url]
        if url_row:
            rows.append(url_row)
    return rows


def _url_rows_from_text(text: str) -> list[list[InlineKeyboardButton]]:
    urls = re.findall(r'href="([^"]+)"', text)
    lot_url = next((url for url in urls if "t.me/nft/" in url), None)
    seller_url = next(
        (
            url
            for url in urls
            if url.startswith("https://t.me/")
            and "t.me/nft/" not in url
            and "syndtrackbot" not in url
        ),
        None,
    )
    row: list[InlineKeyboardButton] = []
    if lot_url:
        row.append(
            InlineKeyboardButton(
                text="Открыть лот",
                url=lot_url,
                icon_custom_emoji_id=emoji_id("gift"),
            )
        )
    if seller_url:
        row.append(
            InlineKeyboardButton(
                text="Продавец",
                url=seller_url,
                icon_custom_emoji_id=emoji_id("profile"),
            )
        )
    return [row] if row else []


def _remove_claim_line(text: str) -> str:
    for marker in ("\n\nВзято:", "\nВзято:", "\n\nЗанято:", "\nЗанято:", "\n\nВоркнут(а) воркером:", "\nВоркнут(а) воркером:"):
        if marker in text:
            return text.split(marker, maxsplit=1)[0]
    return text


def _hide_listing_contacts(text: str) -> str:
    hidden = []
    for line in text.splitlines():
        if "Продавец:" in line:
            hidden.append(f"{tg_emoji('profile')} Продавец: <b>скрыто</b>")
            continue
        if "Сообщения:" in line:
            hidden.append(f"{tg_emoji('megaphone')} Сообщения: <b>скрыто</b>")
            continue
        hidden.append(line)
    return "\n".join(hidden)


def _plan_text(plan, prefix: str | None = None) -> str:
    lines = []
    if prefix:
        lines.extend([prefix, ""])
    lines.extend(
        [
            f"{tg_emoji('money')} <b>Тариф {html.escape(plan.code, quote=False)}</b>",
            f"Срок: <b>{html.escape(plan.title, quote=False)}</b>",
            f"Stars: <b>{plan.stars}</b>",
            f"TON: <b>{plan.ton:g}</b>",
            f"USDT: <b>{plan.usdt:g}</b>",
        ]
    )
    return "\n".join(lines)


def _plan_edit_prompt(field: str) -> str:
    prompts = {
        "duration_seconds": (
            "Пришли новый срок тарифа.\n"
            "Примеры: 6h, 1d, 12 часов, 30 дней.\n"
            "Минимум 1 минута, максимум 366 дней."
        ),
        "stars": "Пришли цену в Telegram Stars целым числом. Например: 750",
        "ton": "Пришли цену в TON. Например: 3 или 3.5",
        "usdt": "Пришли цену в USDT. Например: 10 или 10.5",
    }
    return prompts.get(field, "Пришли новое значение.")


def _normalize_plan_value(field: str, raw: str) -> tuple[str, str]:
    value = raw.strip().replace(" ", "").replace(",", ".")
    if field == "duration_seconds":
        duration = parse_duration(raw)
        seconds = int(duration.total_seconds())
        return str(seconds), f"срок {seconds // 60} мин."
    if field == "stars":
        if not value.isdigit():
            raise ValueError("Цена Stars должна быть целым числом, например 750.")
        stars = int(value)
        if stars <= 0 or stars > 10_000_000:
            raise ValueError("Цена Stars должна быть от 1 до 10000000.")
        return str(stars), f"{stars} Stars"
    if field in {"ton", "usdt"}:
        try:
            amount = float(value)
        except ValueError:
            raise ValueError("Цена должна быть числом, например 3 или 3.5.") from None
        if amount <= 0 or amount > 1_000_000:
            raise ValueError("Цена должна быть больше 0 и не больше 1000000.")
        return f"{amount:g}", f"{amount:g} {field.upper()}"
    raise ValueError("Неизвестное поле тарифа.")


async def _find_user(db: Database, query: str) -> TelegramUser | None:
    value = query.strip().lstrip("@")
    if not value:
        return None
    async with db.session() as session:
        if value.isdigit():
            result = await session.execute(
                select(TelegramUser).where(
                    or_(TelegramUser.telegram_id == int(value), TelegramUser.id == int(value))
                )
            )
        else:
            result = await session.execute(
                select(TelegramUser).where(func.lower(TelegramUser.username) == value.lower())
            )
        return result.scalar_one_or_none()


def _admin_user_text(user: TelegramUser) -> str:
    username = f"@{html.escape(user.username, quote=False)}" if user.username else "нет"
    name = html.escape(" ".join(part for part in (user.first_name, user.last_name) if part) or "нет", quote=False)
    return "\n".join(
        [
            f"{tg_emoji('profile')} <b>Пользователь</b>",
            f"ID: <code>{user.telegram_id}</code>",
            f"Username: {username}",
            f"Имя: {name}",
            f"Premium: {'да' if user.is_premium else 'нет'}",
            f"Триал: {'да' if user.trial_activated else 'нет'}",
            f"Воркнуто логов: {user.claimed_listings_count or 0}",
            f"Статус: {'заблокирован' if user.is_blocked else 'активен'}",
        ]
    )


def _level_label(max_level: int, mode: str) -> str:
    return "> 5" if mode == "gt5" else f"≤ {max_level}"


def _price_label(price: int) -> str:
    if price <= 0:
        return "без лимита"
    return f"≤ {price} Stars"


def _is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids
