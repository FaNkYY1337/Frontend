from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    claimed_listings_count: Mapped[int] = mapped_column(Integer, default=0)
    trial_activated: Mapped[bool] = mapped_column(Boolean, default=False)
    ref_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    referred_by_id: Mapped[int | None] = mapped_column(ForeignKey("telegram_users.id"), nullable=True)
    referral_rewarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    referred_by: Mapped["TelegramUser | None"] = relationship(remote_side=[id])


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("telegram_users.id"), index=True)
    plan_code: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    access_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[TelegramUser] = relationship()


class SubscriptionInvite(Base):
    __tablename__ = "subscription_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("telegram_users.id"), index=True)
    invite_link: Mapped[str] = mapped_column(Text)
    message_chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(Integer)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[TelegramUser] = relationship()


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("telegram_users.id"), index=True)
    plan_code: Mapped[str] = mapped_column(String(32))
    stars: Mapped[int] = mapped_column(Integer)
    ton: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crypto_asset: Mapped[str | None] = mapped_column(String(16), nullable=True)
    crypto_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_payment_charge_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[TelegramUser] = relationship()


class AccountSession(Base):
    __tablename__ = "account_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    session_ciphertext: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class GiftListing(Base):
    __tablename__ = "gift_listings"
    __table_args__ = (UniqueConstraint("listing_key", name="uq_gift_listings_listing_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_key: Mapped[str] = mapped_column(String(255), index=True)
    gift_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    collectible_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_ton: Mapped[float | None] = mapped_column(Float, nullable=True)
    seller_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    seller_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    seller_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    seller_lang_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    seller_is_channel: Mapped[bool] = mapped_column(Boolean, default=False)
    seller_is_premium: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    can_write_free: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    contact_price_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ru_score: Mapped[int] = mapped_column(Integer, default=0)
    ru_reasons: Mapped[str | None] = mapped_column(String(500), nullable=True)
    level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    listed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertedSeller(Base):
    __tablename__ = "alerted_sellers"

    seller_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_key: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BotSetting(Base):
    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
