from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Base


class Database:
    def __init__(self, settings: Settings) -> None:
        self.engine = create_async_engine(settings.database_url, future=True)
        self.sessionmaker = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            autoflush=False,
        )

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

            columns_result = await conn.exec_driver_sql("PRAGMA table_info(gift_listings)")
            columns = {row[1] for row in columns_result.fetchall()}
            migrations = {
                "seller_name": "ALTER TABLE gift_listings ADD COLUMN seller_name VARCHAR(255)",
                "seller_lang_code": "ALTER TABLE gift_listings ADD COLUMN seller_lang_code VARCHAR(32)",
                "seller_is_channel": "ALTER TABLE gift_listings ADD COLUMN seller_is_channel BOOLEAN DEFAULT 0",
                "ru_score": "ALTER TABLE gift_listings ADD COLUMN ru_score INTEGER DEFAULT 0",
                "ru_reasons": "ALTER TABLE gift_listings ADD COLUMN ru_reasons VARCHAR(500)",
                "level": "ALTER TABLE gift_listings ADD COLUMN level INTEGER",
            }
            for column, sql in migrations.items():
                if column not in columns:
                    await conn.exec_driver_sql(sql)

            payment_columns_result = await conn.exec_driver_sql("PRAGMA table_info(payments)")
            payment_columns = {row[1] for row in payment_columns_result.fetchall()}
            payment_migrations = {
                "duration_seconds": "ALTER TABLE payments ADD COLUMN duration_seconds INTEGER",
                "crypto_asset": "ALTER TABLE payments ADD COLUMN crypto_asset VARCHAR(16)",
                "crypto_amount": "ALTER TABLE payments ADD COLUMN crypto_amount FLOAT",
            }
            for column, sql in payment_migrations.items():
                if column not in payment_columns:
                    await conn.exec_driver_sql(sql)

            user_columns_result = await conn.exec_driver_sql("PRAGMA table_info(telegram_users)")
            user_columns = {row[1] for row in user_columns_result.fetchall()}
            user_migrations = {
                "is_blocked": "ALTER TABLE telegram_users ADD COLUMN is_blocked BOOLEAN DEFAULT 0",
                "claimed_listings_count": "ALTER TABLE telegram_users ADD COLUMN claimed_listings_count INTEGER DEFAULT 0",
                "referral_rewarded_at": "ALTER TABLE telegram_users ADD COLUMN referral_rewarded_at DATETIME",
            }
            for column, sql in user_migrations.items():
                if column not in user_columns:
                    await conn.exec_driver_sql(sql)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
