from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from telethon import TelegramClient, functions
from telethon.tl.types import Channel, User
from telethon.sessions import StringSession

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GiftListingData:
    listing_key: str
    gift_id: int | None
    collectible_id: int | None
    slug: str | None
    title: str | None
    model: str | None
    price_stars: int | None
    price_ton: float | None
    seller_id: int | None
    seller_username: str | None
    seller_name: str | None
    seller_lang_code: str | None
    seller_is_channel: bool
    seller_is_premium: bool | None
    can_write_free: bool | None
    contact_price_stars: int | None
    ru_score: int
    ru_reasons: tuple[str, ...]
    level: int
    listed_at: datetime
    raw_json: str


class TelegramGiftMarketClient:
    def __init__(self, settings: Settings, session_string: str) -> None:
        self._settings = settings
        self._session_string = session_string
        self._ru_cache: dict[int, tuple[int, tuple[str, ...]]] = {}
        self._full_user_cache: dict[int, Any | None] = {}
        self._level_cache: dict[int, int] = {}

    async def fetch_latest_listings(self) -> list[GiftListingData]:
        async with TelegramClient(
            StringSession(self._session_string),
            self._settings.telegram_api_id,
            self._settings.telegram_api_hash,
        ) as client:
            gifts_response = await client(functions.payments.GetStarGiftsRequest(hash=0))
            gifts = [
                gift
                for gift in getattr(gifts_response, "gifts", [])
                if getattr(gift, "availability_resale", None)
                or getattr(gift, "resell_min_stars", None)
            ][: self._settings.gift_base_limit]

            listings: list[GiftListingData] = []
            for gift in gifts:
                try:
                    resale = await client(
                        functions.payments.GetResaleStarGiftsRequest(
                            gift_id=gift.id,
                            offset="",
                            limit=self._settings.gift_resale_limit_per_gift,
                            sort_by_price=False,
                            sort_by_num=False,
                            attributes_hash=0,
                            attributes=[],
                        )
                    )
                except Exception as exc:
                    logger.warning("Cannot fetch resale gifts for %s: %s", gift.id, exc)
                    continue

                users_by_id = {
                    getattr(user, "id", None): user for user in getattr(resale, "users", [])
                }
                chats_by_id = {
                    getattr(chat, "id", None): chat for chat in getattr(resale, "chats", [])
                }
                for item in getattr(resale, "gifts", []):
                    listings.append(await self._to_listing(client, item, users_by_id, chats_by_id))
                    if len(listings) >= self._settings.gift_max_listings_per_poll:
                        return listings

            return listings

    async def _to_listing(
        self,
        client: TelegramClient,
        item: Any,
        users_by_id: dict[int | None, Any],
        chats_by_id: dict[int | None, Any],
    ) -> GiftListingData:
        price_stars, price_ton = self._extract_price(item)
        gift_id = getattr(item, "gift_id", None)
        collectible_id = getattr(item, "id", None)
        slug = getattr(item, "slug", None)
        title = getattr(item, "title", None)
        model = self._extract_model(item)
        seller_id = self._extract_owner_user_id(item)
        seller = users_by_id.get(seller_id) or chats_by_id.get(seller_id)
        seller_name = self._seller_name(seller)
        seller_lang_code = getattr(seller, "lang_code", None) if seller else None
        seller_is_channel = isinstance(seller, Channel)
        seller_is_premium = bool(getattr(seller, "premium", False)) if seller else None
        can_write_free, contact_price = await self._contact_requirement(client, seller)
        key_price = price_stars if price_stars is not None else f"ton:{price_ton}"
        listing_key = slug or f"{gift_id}:{collectible_id}:{seller_id}:{key_price}"
        seller_username = await self._seller_username(client, seller, seller_id)
        ru_score, ru_reasons = await self._ru_score(
            client,
            item,
            seller,
            seller_name,
            seller_id,
            seller_username,
        )
        level = await self._seller_level(client, seller, seller_id)

        if price_ton is None and price_stars is not None:
            price_ton = round(price_stars * self._settings.star_to_ton_rate, 4)

        return GiftListingData(
            listing_key=str(listing_key),
            gift_id=gift_id,
            collectible_id=collectible_id,
            slug=slug,
            title=title,
            model=model,
            price_stars=price_stars,
            price_ton=price_ton,
            seller_id=seller_id,
            seller_username=seller_username,
            seller_name=seller_name,
            seller_lang_code=seller_lang_code,
            seller_is_channel=seller_is_channel,
            seller_is_premium=seller_is_premium,
            can_write_free=can_write_free,
            contact_price_stars=contact_price,
            ru_score=ru_score,
            ru_reasons=ru_reasons,
            level=level,
            listed_at=datetime.now(timezone.utc),
            raw_json=json.dumps(_to_primitive(item), ensure_ascii=False),
        )

    def _extract_price(self, item: Any) -> tuple[int | None, float | None]:
        stars_value: int | None = None
        ton_value: float | None = None
        amounts = list(getattr(item, "resell_amount", None) or [])
        for amount in amounts:
            name = amount.__class__.__name__.lower()
            if "starsamount" == name and hasattr(amount, "amount"):
                stars = int(getattr(amount, "amount", 0))
                nanos = int(getattr(amount, "nanos", 0) or 0)
                stars_value = int(round(stars + nanos / 1_000_000_000))
            elif "starstonamount" == name and hasattr(amount, "amount"):
                ton_value = round(int(amount.amount) / 1_000_000_000, 2)

        if stars_value is not None or ton_value is not None:
            return stars_value, ton_value

        value_amount = getattr(item, "value_amount", None)
        value_currency = getattr(item, "value_currency", None)
        if value_amount is not None and value_currency == "TON":
            return None, round(int(value_amount) / 1_000_000_000, 2)
        return None, None

    def _extract_model(self, item: Any) -> str | None:
        for attribute in getattr(item, "attributes", []) or []:
            if attribute.__class__.__name__ == "StarGiftAttributeModel":
                return getattr(attribute, "name", None)
        return None

    def _extract_owner_user_id(self, item: Any) -> int | None:
        owner = getattr(item, "owner_id", None)
        if owner is None:
            return None
        return getattr(owner, "user_id", None) or getattr(owner, "channel_id", None)

    def _seller_name(self, seller: Any | None) -> str | None:
        if seller is None:
            return None
        if getattr(seller, "title", None):
            return getattr(seller, "title", None)
        parts = [
            getattr(seller, "first_name", None),
            getattr(seller, "last_name", None),
        ]
        name = " ".join(part for part in parts if part)
        return name or None

    async def _ru_score(
        self,
        client: TelegramClient,
        item: Any,
        seller: Any | None,
        seller_name: str | None,
        seller_id: int | None,
        seller_username: str | None = None,
    ) -> tuple[int, tuple[str, ...]]:
        if seller_id is not None and seller_id in self._ru_cache:
            cached_score, cached_reasons = self._ru_cache[seller_id]
        else:
            cached_score = 0
            cached_reasons = ()

        score = 0
        reasons: list[str] = []
        seller_username = seller_username or (getattr(seller, "username", None) if seller else None)
        seller_lang_code = getattr(seller, "lang_code", None) if seller else None
        owner_name = getattr(item, "owner_name", None)
        text_blob = " ".join(
            str(value)
            for value in (
                seller_name,
                seller_username,
                seller_lang_code,
                owner_name,
                getattr(item, "title", None),
                self._extract_model(item),
            )
            if value
        )

        if seller_lang_code and seller_lang_code.lower().startswith("ru"):
            score += 3
            reasons.append("язык профиля ru")
        if seller_name and _has_cyrillic(seller_name):
            score += 4
            reasons.append("кириллица в имени")
        if seller_name and _looks_ru_translit(seller_name):
            score += 3
            reasons.append("русское имя латиницей")
        if owner_name and _has_cyrillic(owner_name):
            score += 3
            reasons.append("кириллица в подписи владельца")
        if _has_cyrillic(text_blob):
            score += 2
            reasons.append("русский текст в данных")
        if seller_username and _looks_ru_username(seller_username):
            score += 2
            reasons.append("RU-паттерн в username")
        if _looks_ru_translit(text_blob):
            score += 3
            reasons.append("русское имя латиницей")

        if score < self._settings.gift_ru_min_score:
            if seller_id is not None and not cached_reasons:
                profile_score, profile_reasons = await self._profile_ru_score(client, seller)
                cached_score = profile_score
                cached_reasons = profile_reasons
                self._ru_cache[seller_id] = (cached_score, cached_reasons)

            score += cached_score
            reasons.extend(cached_reasons)

        return score, tuple(dict.fromkeys(reasons))

    async def _profile_ru_score(
        self,
        client: TelegramClient,
        seller: Any | None,
    ) -> tuple[int, tuple[str, ...]]:
        if seller is None:
            return 0, ()

        score = 0
        reasons: list[str] = []
        if isinstance(seller, User):
            try:
                full = await self._get_full_user(client, seller)
                about = getattr(getattr(full, "full_user", None), "about", None)
                if about and _has_cyrillic(about):
                    score += 4
                    reasons.append("кириллица в описании профиля")
                elif about and _looks_ru_translit(about):
                    score += 2
                    reasons.append("русский транслит в описании профиля")
            except Exception:
                pass

        if isinstance(seller, Channel):
            try:
                input_entity = await client.get_input_entity(seller)
            except Exception:
                return score, tuple(dict.fromkeys(reasons))

            try:
                full = await client(functions.channels.GetFullChannelRequest(channel=input_entity))
                about = getattr(getattr(full, "full_chat", None), "about", None)
                if about and _has_cyrillic(about):
                    score += 5
                    reasons.append("русское описание канала")
                elif about and _looks_ru_translit(about):
                    score += 2
                    reasons.append("русский транслит в описании канала")
            except Exception:
                pass
            try:
                messages = await client.get_messages(input_entity, limit=8)
                texts = " ".join(getattr(message, "message", "") or "" for message in messages)
                if _has_cyrillic(texts):
                    score += 6
                    reasons.append("русские посты в канале")
            except Exception:
                pass

        return score, tuple(dict.fromkeys(reasons))

    async def _get_full_user(self, client: TelegramClient, seller: User) -> Any | None:
        seller_id = getattr(seller, "id", None)
        if seller_id is not None and seller_id in self._full_user_cache:
            return self._full_user_cache[seller_id]

        full: Any | None = None
        try:
            input_entity = await client.get_input_entity(seller)
            full = await client(functions.users.GetFullUserRequest(id=input_entity))
        except Exception:
            full = None

        if seller_id is not None:
            self._full_user_cache[seller_id] = full
        return full

    async def _seller_level(
        self,
        client: TelegramClient,
        seller: Any | None,
        seller_id: int | None,
    ) -> int:
        if seller_id is None or not isinstance(seller, User):
            return -1
        if seller_id in self._level_cache:
            return self._level_cache[seller_id]

        level = -1
        full = await self._get_full_user(client, seller)
        rating = getattr(getattr(full, "full_user", None), "stars_rating", None)
        raw_level = getattr(rating, "level", None)
        if raw_level is not None:
            try:
                level = int(raw_level)
            except (TypeError, ValueError):
                level = -1

        self._level_cache[seller_id] = level
        return level

    async def _seller_username(
        self,
        client: TelegramClient,
        seller: Any | None,
        seller_id: int | None,
    ) -> str | None:
        username = getattr(seller, "username", None) if seller else None
        if username:
            return str(username).lstrip("@")
        if seller_id is None or not isinstance(seller, User):
            return None

        full = await self._get_full_user(client, seller)
        for user in getattr(full, "users", []) or []:
            if getattr(user, "id", None) == seller_id and getattr(user, "username", None):
                return str(user.username).lstrip("@")

        try:
            entity = await client.get_entity(seller)
        except Exception:
            return None
        entity_username = getattr(entity, "username", None)
        return str(entity_username).lstrip("@") if entity_username else None

    async def _contact_requirement(
        self,
        client: TelegramClient,
        seller: Any | None,
    ) -> tuple[bool | None, int | None]:
        if seller is None:
            return None, None

        paid_messages_stars = getattr(seller, "send_paid_messages_stars", None)
        if not self._settings.gift_check_contact_requirements:
            if paid_messages_stars:
                return False, int(paid_messages_stars)
            return True, None

        try:
            input_user = await client.get_input_entity(seller)
            requirements = await client(
                functions.users.GetRequirementsToContactRequest(id=[input_user])
            )
        except Exception:
            return None, paid_messages_stars

        if not requirements:
            return None, None

        requirement = requirements[0]
        name = requirement.__class__.__name__
        if name == "RequirementToContactEmpty":
            return True, None
        if name == "RequirementToContactPaidMessages":
            return False, int(getattr(requirement, "stars_amount", 0))
        if name == "RequirementToContactPremium":
            return False, None
        return None, getattr(requirement, "stars_amount", None)


def _to_primitive(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): _to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_primitive(item) for item in value]
    if hasattr(value, "to_dict"):
        return _to_primitive(value.to_dict())
    return str(value)


def _has_cyrillic(value: str) -> bool:
    return any("а" <= char.lower() <= "я" or char.lower() == "ё" for char in value)


def _looks_ru_username(username: str) -> bool:
    lowered = username.lower()
    markers = (
        "ru",
        "rus",
        "russia",
        "moscow",
        "msk",
        "spb",
        "sasha",
        "dima",
        "misha",
        "nikita",
        "vanya",
        "ivan",
        "katya",
        "masha",
        "kirill",
        "egor",
        "artem",
        "sergey",
    )
    return any(marker in lowered for marker in markers)


def _looks_ru_translit(value: str) -> bool:
    lowered = value.lower()
    names = (
        "nikita",
        "dmitry",
        "dmitriy",
        "dima",
        "sasha",
        "alexey",
        "alexei",
        "aleksey",
        "misha",
        "mikhail",
        "ivan",
        "vanya",
        "katya",
        "ekaterina",
        "kirill",
        "egor",
        "artem",
        "artyom",
        "sergey",
        "sergei",
        "andrey",
        "andrei",
        "ilya",
        "maxim",
        "maksim",
        "nastya",
        "anastasia",
        "polina",
        "daria",
        "karina",
        "alina",
        "arina",
        "vlad",
        "vladimir",
        "pavel",
        "roman",
        "ruslan",
        "timur",
        "danil",
        "danila",
    )
    words = (
        "privet",
        "spasibo",
        "prodazha",
        "pokupka",
        "podarok",
        "podarki",
        "moscow",
        "piter",
    )
    return any(token in lowered for token in names + words)
