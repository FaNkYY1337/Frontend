from __future__ import annotations

from urllib.parse import quote_plus

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import AccountSession, TelegramUser
from app.plans import Plan
from app.premium_emoji import emoji_id


def main_menu(show_trial: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Мой профиль", callback_data="profile", icon_custom_emoji_id=emoji_id("profile"))
    builder.button(text="Подписка", callback_data="subscriptions", icon_custom_emoji_id=emoji_id("gift"))
    builder.button(text="Реферальная программа", callback_data="referral", icon_custom_emoji_id=emoji_id("people"))
    if show_trial:
        builder.button(
            text="Попробовать бесплатно",
            callback_data="trial",
            icon_custom_emoji_id=emoji_id("party"),
        )
    builder.adjust(1)
    return builder.as_markup()


def profile_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Оформить подписку",
        callback_data="subscriptions",
        icon_custom_emoji_id=emoji_id("gift"),
    )
    builder.button(text="В меню", callback_data="home", icon_custom_emoji_id=emoji_id("home"))
    builder.adjust(1)
    return builder.as_markup()


def referral_menu(link: str) -> InlineKeyboardMarkup:
    share_url = f"https://t.me/share/url?url={quote_plus(link)}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Поделиться ссылкой",
                    url=share_url,
                    icon_custom_emoji_id=emoji_id("send"),
                )
            ],
            [InlineKeyboardButton(text="В меню", callback_data="home", icon_custom_emoji_id=emoji_id("home"))],
        ]
    )


def expired_subscription_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Оформить подписку",
        callback_data="subscriptions",
        icon_custom_emoji_id=emoji_id("gift"),
    )
    builder.button(
        text="Попробовать бесплатно",
        callback_data="trial",
        icon_custom_emoji_id=emoji_id("party"),
    )
    builder.adjust(1)
    return builder.as_markup()


def subscriptions_menu(plans: list[Plan]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.button(
            text=f"{plan.title}: {plan.ton:g} TON / {plan.stars} Stars",
            callback_data=f"buy:{plan.code}",
            icon_custom_emoji_id=emoji_id("money"),
        )
    builder.button(text="Назад", callback_data="home")
    builder.adjust(1)
    return builder.as_markup()


def buy_menu(plan_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Оплатить Stars",
                    callback_data=f"pay_stars:{plan_code}",
                    icon_custom_emoji_id=emoji_id("send_money"),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Оплатить CryptoBot",
                    callback_data=f"crypto_asset:{plan_code}",
                    icon_custom_emoji_id=emoji_id("crypto"),
                )
            ],
            [InlineKeyboardButton(text="Назад", callback_data="subscriptions")],
        ]
    )


def crypto_asset_menu(plan_code: str, ton: float, usdt: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"TON: {ton:g}",
                    callback_data=f"pay_crypto:{plan_code}:TON",
                    icon_custom_emoji_id=emoji_id("crypto"),
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"USDT: {usdt:g}",
                    callback_data=f"pay_crypto:{plan_code}:USDT",
                    icon_custom_emoji_id=emoji_id("money"),
                )
            ],
            [InlineKeyboardButton(text="Назад", callback_data=f"buy:{plan_code}")],
        ]
    )


def crypto_invoice_menu(payment_id: int, pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть оплату",
                    url=pay_url,
                    icon_custom_emoji_id=emoji_id("crypto"),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Проверить оплату",
                    callback_data=f"check_crypto:{payment_id}",
                    icon_custom_emoji_id=emoji_id("ok"),
                )
            ],
            [InlineKeyboardButton(text="Назад", callback_data="subscriptions")],
        ]
    )


def admin_menu(
    parser_enabled: bool = True,
    level: int = 2,
    messages_label: str = "без звёзд",
    max_price_label: str = "без лимита",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Остановить парсер" if parser_enabled else "Запустить парсер",
        callback_data="admin:toggle_monitor",
        icon_custom_emoji_id=emoji_id("cross" if parser_enabled else "ok"),
    )
    level_label = "> 5" if level < 0 else f"≤ {level}"
    builder.button(text=f"Level: {level_label}", callback_data="admin:level", icon_custom_emoji_id=emoji_id("chart_up"))
    builder.button(text=f"Цена: {max_price_label}", callback_data="admin:max_price", icon_custom_emoji_id=emoji_id("money"))
    builder.button(text=f"Сообщения: {messages_label}", callback_data="admin:messages", icon_custom_emoji_id=emoji_id("megaphone"))
    builder.button(text="Пользователи", callback_data="admin:users", icon_custom_emoji_id=emoji_id("people"))
    builder.button(text="Сессии", callback_data="admin:sessions", icon_custom_emoji_id=emoji_id("file"))
    builder.button(text="Статистика", callback_data="admin:stats", icon_custom_emoji_id=emoji_id("stats"))
    builder.button(text="Тарифы", callback_data="admin:plans", icon_custom_emoji_id=emoji_id("money"))
    builder.button(text="Рассылка", callback_data="admin:broadcast", icon_custom_emoji_id=emoji_id("send"))
    builder.button(text="Обновить", callback_data="admin", icon_custom_emoji_id=emoji_id("loading"))
    builder.adjust(1, 2, 1, 2, 2, 1, 1)
    return builder.as_markup()


def admin_plans_menu(plans: list[Plan]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.button(
            text=f"{plan.title}: {plan.stars} Stars / {plan.ton:g} TON / {plan.usdt:g} USDT",
            callback_data=f"admin:plan:{plan.code}",
            icon_custom_emoji_id=emoji_id("money"),
        )
    builder.button(text="Назад", callback_data="admin")
    builder.adjust(1)
    return builder.as_markup()


def admin_plan_menu(plan: Plan) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Срок: {plan.title}", callback_data=f"admin:plan_edit:{plan.code}:duration_seconds", icon_custom_emoji_id=emoji_id("time"))
    builder.button(text=f"Stars: {plan.stars}", callback_data=f"admin:plan_edit:{plan.code}:stars", icon_custom_emoji_id=emoji_id("send_money"))
    builder.button(text=f"TON: {plan.ton:g}", callback_data=f"admin:plan_edit:{plan.code}:ton", icon_custom_emoji_id=emoji_id("crypto"))
    builder.button(text=f"USDT: {plan.usdt:g}", callback_data=f"admin:plan_edit:{plan.code}:usdt", icon_custom_emoji_id=emoji_id("money"))
    builder.button(text="Назад", callback_data="admin:plans")
    builder.adjust(1)
    return builder.as_markup()


def level_menu(current: int, mode: str = "max") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for level in (1, 2, 3, 4, 5):
        builder.button(
            text=f"Level: ≤ {level}",
            callback_data=f"admin:set_level:{level}",
            icon_custom_emoji_id=emoji_id("ok" if mode == "max" and current == level else "chart_up"),
        )
    builder.button(
        text="Level: > 5",
        callback_data="admin:set_level:gt5",
        icon_custom_emoji_id=emoji_id("ok" if mode == "gt5" else "chart_up"),
    )
    builder.button(text="Назад", callback_data="admin")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def admin_back_menu(back_to: str = "admin") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data=back_to)]]
    )


def admin_stats_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Обновить", callback_data="admin:stats", icon_custom_emoji_id=emoji_id("loading"))],
            [InlineKeyboardButton(text="Назад", callback_data="admin")],
        ]
    )


def admin_users_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Найти пользователя", callback_data="admin:user_search", icon_custom_emoji_id=emoji_id("profile"))],
            [InlineKeyboardButton(text="Назад", callback_data="admin")],
        ]
    )


def admin_user_menu(user: TelegramUser) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Разблокировать" if user.is_blocked else "Заблокировать",
                    callback_data=f"admin:user_toggle_block:{user.id}",
                    icon_custom_emoji_id=emoji_id("unlock" if user.is_blocked else "lock"),
                )
            ],
            [InlineKeyboardButton(text="Написать", callback_data=f"admin:user_message:{user.id}", icon_custom_emoji_id=emoji_id("send"))],
            [InlineKeyboardButton(text="Выдать подписку", callback_data=f"admin:user_sub:{user.id}", icon_custom_emoji_id=emoji_id("gift"))],
            [InlineKeyboardButton(text="Назад", callback_data="admin:users")],
        ]
    )


def max_price_menu(current: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    presets = (1000, 2500, 5000, 10000, 20000, 50000)
    for price in presets:
        builder.button(
            text=f"≤ {price} Stars",
            callback_data=f"admin:set_max_price:{price}",
            icon_custom_emoji_id=emoji_id("ok" if current == price else "money"),
        )
    builder.button(
        text="Без лимита",
        callback_data="admin:set_max_price:0",
        icon_custom_emoji_id=emoji_id("ok" if current <= 0 else "unlock"),
    )
    builder.button(text="Ввести вручную", callback_data="admin:max_price:custom", icon_custom_emoji_id=emoji_id("pencil"))
    builder.button(text="Назад", callback_data="admin")
    builder.adjust(2, 2, 2, 1, 1, 1)
    return builder.as_markup()


def messages_filter_menu(current: str) -> InlineKeyboardMarkup:
    labels = {
        "free": "без звёзд",
        "paid_allowed": "можно со звёздами",
        "paid_only": "только звёзды",
    }
    builder = InlineKeyboardBuilder()
    for mode, label in labels.items():
        builder.button(
            text=label,
            callback_data=f"admin:set_messages:{mode}",
            icon_custom_emoji_id=emoji_id("ok" if current == mode else "megaphone"),
        )
    builder.button(text="Назад", callback_data="admin")
    builder.adjust(1)
    return builder.as_markup()


def sessions_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Добавить по телефону", callback_data="admin:add_session_phone", icon_custom_emoji_id=emoji_id("profile"))
    builder.button(text="Добавить StringSession", callback_data="admin:add_session_string", icon_custom_emoji_id=emoji_id("code"))
    builder.button(text="Список сессий", callback_data="admin:list_sessions", icon_custom_emoji_id=emoji_id("file"))
    builder.button(text="Назад", callback_data="admin")
    builder.adjust(1)
    return builder.as_markup()


def session_item_menu(session_id: int, is_active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    action = "disable" if is_active else "enable"
    label = "Выключить" if is_active else "Включить"
    builder.button(text=label, callback_data=f"admin:session:{action}:{session_id}", icon_custom_emoji_id=emoji_id("lock" if is_active else "unlock"))
    builder.button(text="Удалить", callback_data=f"admin:session:delete:{session_id}", icon_custom_emoji_id=emoji_id("trash"))
    builder.button(text="Назад", callback_data="admin:list_sessions")
    builder.adjust(1)
    return builder.as_markup()


def sessions_list_menu(sessions: list[AccountSession]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for session in sessions:
        status = "on" if session.is_active else "off"
        builder.button(
            text=f"{status} #{session.id} {session.name}",
            callback_data=f"admin:session:view:{session.id}",
            icon_custom_emoji_id=emoji_id("unlock" if session.is_active else "lock"),
        )
    builder.button(text="Назад", callback_data="admin:sessions")
    builder.adjust(1)
    return builder.as_markup()


def code_keypad_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for digit in range(1, 10):
        builder.button(text=str(digit), callback_data=f"login_code:digit:{digit}")
    builder.button(text="Удалить", callback_data="login_code:backspace", icon_custom_emoji_id=emoji_id("trash"))
    builder.button(text="0", callback_data="login_code:digit:0")
    builder.button(text="OK", callback_data="login_code:submit", icon_custom_emoji_id=emoji_id("ok"))
    builder.button(text="Сброс", callback_data="login_code:clear", icon_custom_emoji_id=emoji_id("cross"))
    builder.adjust(3, 3, 3, 3, 1)
    return builder.as_markup()


def confirm_broadcast_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Отправить", callback_data="admin:broadcast:confirm", icon_custom_emoji_id=emoji_id("send"))
    builder.button(text="Отмена", callback_data="admin:broadcast:cancel", icon_custom_emoji_id=emoji_id("cross"))
    builder.adjust(1)
    return builder.as_markup()
