from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")

    private_chat_id: int = Field(alias="PRIVATE_CHAT_ID")
    listings_chat_id: int = Field(alias="LISTINGS_CHAT_ID")

    telegram_api_id: int = Field(alias="TELEGRAM_API_ID")
    telegram_api_hash: str = Field(alias="TELEGRAM_API_HASH")
    session_secret: str = Field(alias="SESSION_SECRET")

    database_url: str = Field(default="sqlite+aiosqlite:///bot.db", alias="DATABASE_URL")
    timezone: str = Field(default="Asia/Novosibirsk", alias="TIMEZONE")

    star_to_ton_rate: float = Field(default=0.004, alias="STAR_TO_TON_RATE")
    gift_poll_interval_seconds: int = Field(default=20, alias="GIFT_POLL_INTERVAL_SECONDS")
    gift_resale_limit_per_gift: int = Field(default=2, alias="GIFT_RESALE_LIMIT_PER_GIFT")
    gift_base_limit: int = Field(default=60, alias="GIFT_BASE_LIMIT")
    gift_max_listings_per_poll: int = Field(default=60, alias="GIFT_MAX_LISTINGS_PER_POLL")
    gift_check_contact_requirements: bool = Field(
        default=False,
        alias="GIFT_CHECK_CONTACT_REQUIREMENTS",
    )
    gift_require_seller_username: bool = Field(
        default=True,
        alias="GIFT_REQUIRE_SELLER_USERNAME",
    )
    gift_ru_only: bool = Field(default=True, alias="GIFT_RU_ONLY")
    gift_ru_min_score: int = Field(default=2, alias="GIFT_RU_MIN_SCORE")
    listing_send_delay_seconds: float = Field(default=0.0, alias="LISTING_SEND_DELAY_SECONDS")
    invite_ttl_minutes: int = Field(default=30, alias="INVITE_TTL_MINUTES")
    trial_hours: int = Field(default=3, alias="TRIAL_HOURS")
    support_username: str | None = Field(default=None, alias="SUPPORT_USERNAME")
    creator_username: str = Field(default="creator_username", alias="CREATOR_USERNAME")
    cryptobot_token: str | None = Field(default=None, alias="CRYPTOBOT_TOKEN")
    cryptobot_api_url: str = Field(default="https://pay.crypt.bot/api", alias="CRYPTOBOT_API_URL")

    @property
    def admin_ids(self) -> list[int]:
        return [int(item.strip()) for item in self.admin_ids_raw.split(",") if item.strip()]

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
