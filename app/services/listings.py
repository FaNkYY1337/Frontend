import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.models import AccountSession, AlertedSeller, GiftListing
from app.premium_emoji import emoji_id, tg_emoji
from app.security import SessionCipher
from app.services.gift_market.telegram import GiftListingData, TelegramGiftMarketClient
from app.services.settings import get_setting, monitor_enabled, set_setting

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FormattedListing:
    text: str
    data: GiftListingData


async def poll_gift_market(
    bot: Bot,
    db,
    settings: Settings,
    cipher: SessionCipher,
    force: bool = False,
) -> None:
    async with db.session() as session:
        if not force and not await monitor_enabled(session):
            return

        result = await session.execute(
            select(AccountSession).where(AccountSession.is_active.is_(True))
        )
        account_sessions = result.scalars().all()

    for account_session in account_sessions:
        try:
            session_string = cipher.decrypt(account_session.session_ciphertext)
            client = TelegramGiftMarketClient(settings, session_string)
            listings = await client.fetch_latest_listings()
            logger.info(
                "Fetched %s gift listings with session #%s",
                len(listings),
                account_session.id,
            )
        except Exception as exc:
            async with db.session() as session:
                stored = await session.get(AccountSession, account_session.id)
                if stored:
                    stored.last_error = str(exc)
                await set_setting(session, "last_poll_error", str(exc))
            logger.exception("Cannot fetch gift listings with session #%s", account_session.id)
            continue

        sent = 0
        skipped_duplicates = 0
        skipped_ru = 0
        await _set_session_error(db, account_session.id, None)
        for listing in listings:
            if listing.seller_is_channel:
                skipped_ru += 1
                logger.info(
                    "Skip listing %s because seller is a channel",
                    listing.listing_key,
                )
                continue
            if settings.gift_require_seller_username and not listing.seller_username:
                skipped_ru += 1
                logger.info(
                    "Skip listing %s because seller has no username",
                    listing.listing_key,
                )
                continue
            if await _listing_exists(db, listing.listing_key):
                skipped_duplicates += 1
                continue
            if listing.seller_id and await _seller_already_alerted(db, listing.seller_id):
                skipped_duplicates += 1
                logger.info(
                    "Skip listing %s because seller %s was already alerted",
                    listing.listing_key,
                    listing.seller_id,
                )
                continue
            if settings.gift_ru_only and listing.ru_score < settings.gift_ru_min_score:
                skipped_ru += 1
                logger.info(
                    "Skip non-RU listing %s with score %s",
                    listing.listing_key,
                    listing.ru_score,
                )
                continue
            if not await _passes_runtime_filters(db, listing, settings):
                skipped_ru += 1
                logger.info("Skip listing %s by runtime filters", listing.listing_key)
                continue

            await _send_listing_message(bot, settings, listing)
            await _remember_listing(db, listing)
            if listing.seller_id:
                await _remember_alerted_seller(db, listing.seller_id, listing.listing_key)
            sent += 1
            if settings.listing_send_delay_seconds > 0:
                await asyncio.sleep(settings.listing_send_delay_seconds)
        if sent:
            logger.info("Sent %s new gift listings with session #%s", sent, account_session.id)
        await _store_poll_stats(
            db,
            fetched=len(listings),
            sent=sent,
            skipped_duplicates=skipped_duplicates,
            skipped_ru=skipped_ru,
            error=None,
        )


def _listing_to_model(listing: GiftListingData) -> GiftListing:
    return GiftListing(
        listing_key=listing.listing_key,
        gift_id=listing.gift_id,
        collectible_id=listing.collectible_id,
        slug=listing.slug,
        title=listing.title,
        model=listing.model,
        price_stars=listing.price_stars,
        price_ton=listing.price_ton,
        seller_id=listing.seller_id,
        seller_username=listing.seller_username,
        seller_name=listing.seller_name,
        seller_lang_code=listing.seller_lang_code,
        seller_is_channel=listing.seller_is_channel,
        seller_is_premium=listing.seller_is_premium,
        can_write_free=listing.can_write_free,
        contact_price_stars=listing.contact_price_stars,
        ru_score=listing.ru_score,
        ru_reasons=", ".join(listing.ru_reasons) if listing.ru_reasons else None,
        level=listing.level,
        listed_at=listing.listed_at,
        raw_json=listing.raw_json,
    )


async def _remember_listing(db, listing: GiftListingData) -> bool:
    try:
        async with db.session() as session:
            exists = await session.execute(
                select(GiftListing.id).where(GiftListing.listing_key == listing.listing_key)
            )
            if exists.scalar_one_or_none() is not None:
                return False
            session.add(_listing_to_model(listing))
        return True
    except IntegrityError:
        return False


async def _listing_exists(db, listing_key: str) -> bool:
    async with db.session() as session:
        exists = await session.execute(
            select(GiftListing.id).where(GiftListing.listing_key == listing_key)
        )
        return exists.scalar_one_or_none() is not None


async def _seller_already_alerted(db, seller_id: int) -> bool:
    async with db.session() as session:
        exists = await session.get(AlertedSeller, seller_id)
        return exists is not None


async def _remember_alerted_seller(db, seller_id: int, listing_key: str) -> None:
    try:
        async with db.session() as session:
            if await session.get(AlertedSeller, seller_id) is None:
                session.add(AlertedSeller(seller_id=seller_id, listing_key=listing_key))
    except IntegrityError:
        return


async def _passes_runtime_filters(db, listing: GiftListingData, settings: Settings) -> bool:
    async with db.session() as session:
        max_level = int(await get_setting(session, "filter_max_level", "2") or "2")
        level_mode = await get_setting(session, "filter_level_mode", "max")
        max_price = int(await get_setting(session, "filter_max_price_stars", "0") or "0")
        messages_mode = await get_setting(session, "filter_messages_mode", "free")

    if level_mode == "gt5" and listing.level <= 5:
        return False
    if level_mode != "gt5" and listing.level > max_level:
        return False
    if max_price > 0:
        price_stars = _listing_price_stars(listing, settings)
        if price_stars is not None and price_stars > max_price:
            return False

    if messages_mode == "free":
        return listing.can_write_free is True and not listing.contact_price_stars
    if messages_mode == "paid_allowed":
        return True
    if messages_mode == "paid_only":
        return bool(listing.contact_price_stars) or listing.can_write_free is False
    return True


def _listing_price_stars(listing: GiftListingData, settings: Settings) -> int | None:
    if listing.price_stars is not None:
        return listing.price_stars
    if listing.price_ton is None or settings.star_to_ton_rate <= 0:
        return None
    return round(listing.price_ton / settings.star_to_ton_rate)


async def _set_session_error(db, session_id: int, error: str | None) -> None:
    async with db.session() as session:
        stored = await session.get(AccountSession, session_id)
        if stored:
            stored.last_error = error


async def _store_poll_stats(
    db,
    fetched: int,
    sent: int,
    skipped_duplicates: int,
    skipped_ru: int,
    error: str | None,
) -> None:
    async with db.session() as session:
        await set_setting(session, "last_poll_fetched", str(fetched))
        await set_setting(session, "last_poll_sent", str(sent))
        await set_setting(session, "last_poll_skipped_duplicates", str(skipped_duplicates))
        await set_setting(session, "last_poll_skipped_ru", str(skipped_ru))
        await set_setting(session, "last_poll_error", error or "")
        await set_setting(session, "last_poll_at", datetime.now().isoformat(timespec="seconds"))


async def _send_listing_message(bot: Bot, settings: Settings, listing: GiftListingData) -> None:
    for attempt in range(3):
        try:
            await bot.send_message(
                chat_id=settings.listings_chat_id,
                text=format_listing_message(listing, settings),
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=listing_keyboard(listing),
            )
            return
        except TelegramRetryAfter as exc:
            wait_seconds = float(exc.retry_after) + 1
            logger.warning(
                "Flood limit while sending %s; sleeping %.1f seconds",
                listing.listing_key,
                wait_seconds,
            )
            await asyncio.sleep(wait_seconds)
        except TelegramBadRequest as exc:
            if "DOCUMENT_INVALID" not in str(exc):
                raise
            logger.warning(
                "Telegram rejected premium emoji for %s; retrying without custom emoji",
                listing.listing_key,
            )
            await bot.send_message(
                chat_id=settings.listings_chat_id,
                text=format_listing_message(listing, settings, use_premium_emoji=False),
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=listing_keyboard(listing, use_premium_emoji=False),
            )
            return
    await bot.send_message(
        chat_id=settings.listings_chat_id,
        text=format_listing_message(listing, settings),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=listing_keyboard(listing),
    )


def format_listing_message(
    listing: GiftListingData,
    settings: Settings,
    use_premium_emoji: bool = True,
) -> str:
    seller = "неизвестно"
    if listing.seller_username:
        username = escape(listing.seller_username)
        seller = f'<a href="https://t.me/{username}">@{username}</a>'
    elif listing.seller_name:
        seller = escape(listing.seller_name)
    if listing.seller_id:
        seller = f"{seller} ({listing.seller_id})"

    if listing.can_write_free is True:
        contact = "бесплатно"
    elif listing.contact_price_stars:
        contact = f"{listing.contact_price_stars} Stars"
    elif listing.can_write_free is False:
        contact = "платно"
    else:
        contact = "не удалось проверить"

    premium = "Premium" if listing.seller_is_premium else "без Premium"
    if listing.seller_is_premium is None:
        premium = "Premium неизвестен"

    price = []
    if listing.price_stars is not None:
        price.append(f"{listing.price_stars} Stars")
    if listing.price_ton is not None:
        price.append(f"{listing.price_ton:.2f} TON")

    lot_name = listing.slug or listing.title or "лот"
    listed_at = listing.listed_at.astimezone(settings.tzinfo).strftime("%d.%m.%Y %H:%M:%S")
    gift_url = f"https://t.me/nft/{listing.slug}" if listing.slug else None
    gift_title = escape(listing.title or "неизвестно")
    gift_value = f'<a href="{gift_url}"><b>{gift_title}</b></a>' if gift_url else f"<b>{gift_title}</b>"
    slug_value = f'<a href="{gift_url}">{escape(lot_name)}</a>' if gift_url else escape(lot_name)

    lines = [
        '<a href="https://t.me/syndtrackbot"><b>Gift Tracker</b></a>',
        f"{premium_emoji('party', use_premium_emoji)} <b>НОВЫЙ ЛИСТИНГ</b>",
        "",
        f"{premium_emoji('gift', use_premium_emoji)} Гифт: {gift_value}",
        f"{premium_emoji('money', use_premium_emoji)} Цена: <b>{escape(' / '.join(price) if price else 'неизвестно')}</b>",
        f"{premium_emoji('tag', use_premium_emoji)} Модель: <b>{escape(listing.model or 'неизвестно')}</b>",
        f"{premium_emoji('profile', use_premium_emoji)} Продавец: <b>{seller}</b>",
        f"{premium_emoji('chart_up', use_premium_emoji)} Level: <b>{listing.level}</b>",
        f"{premium_emoji('megaphone', use_premium_emoji)} Сообщения: <b>{escape(contact)}</b>",
        f"{premium_emoji('user_ok', use_premium_emoji)} Статус: <b>{escape(premium)}</b>",
        f"{premium_emoji('link', use_premium_emoji)} {slug_value}",
        f"{premium_emoji('time', use_premium_emoji)} {listed_at}",
        "",
        f"<i>Creator By @{escape(settings.creator_username)}</i>",
    ]
    return "\n".join(lines)


def listing_keyboard(
    listing: GiftListingData,
    use_premium_emoji: bool = True,
) -> InlineKeyboardMarkup | None:
    keyboard: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="Занять",
                callback_data=f"listing_claim:{listing.listing_key[:45]}",
                icon_custom_emoji_id=emoji_id("ok") if use_premium_emoji else None,
            )
        ]
    ]
    row: list[InlineKeyboardButton] = []
    if listing.slug:
        row.append(
            InlineKeyboardButton(
                text="Открыть лот",
                url=f"https://t.me/nft/{listing.slug}",
                icon_custom_emoji_id=emoji_id("gift") if use_premium_emoji else None,
            )
        )
    if listing.seller_username:
        row.append(
            InlineKeyboardButton(
                text="Продавец",
                url=f"https://t.me/{listing.seller_username}",
                icon_custom_emoji_id=emoji_id("profile") if use_premium_emoji else None,
            )
        )
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def escape(value: str) -> str:
    return html.escape(value, quote=False)


def premium_emoji(name: str, use_premium_emoji: bool = True) -> str:
    if use_premium_emoji:
        return tg_emoji(name)
    return ""


def fake_listing(settings: Settings) -> FormattedListing:
    listing = GiftListingData(
        listing_key="test",
        gift_id=1,
        collectible_id=1001,
        slug=None,
        title="Victoria Medal",
        model="Silver",
        price_stars=250,
        price_ton=round(250 * settings.star_to_ton_rate, 4),
        seller_id=123456789,
        seller_username="seller",
        seller_name="Иван",
        seller_lang_code="ru",
        seller_is_channel=False,
        seller_is_premium=True,
        can_write_free=False,
        contact_price_stars=5,
        ru_score=7,
        ru_reasons=("кириллица в имени", "язык профиля ru"),
        level=2,
        listed_at=datetime.now(settings.tzinfo),
        raw_json="{}",
    )
    return FormattedListing(text=format_listing_message(listing, settings), data=listing)
