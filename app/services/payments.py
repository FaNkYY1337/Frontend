from __future__ import annotations

import secrets
from datetime import timedelta

import aiohttp
from aiogram import Bot
from aiogram.types import LabeledPrice
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Payment, TelegramUser
from app.plans import crypto_amount, format_duration, get_plan


def make_payment_payload(plan_code: str) -> str:
    return f"sub:{plan_code}:{secrets.token_urlsafe(12)}"


async def create_payment(
    session: AsyncSession,
    user: TelegramUser,
    plan_code: str,
    crypto_asset: str | None = None,
) -> Payment:
    plan = await get_plan(session, plan_code)
    crypto_asset = crypto_asset.upper() if crypto_asset else None
    amount = crypto_amount(plan, crypto_asset) if crypto_asset else None
    payment = Payment(
        user_id=user.id,
        plan_code=plan.code,
        stars=plan.stars,
        ton=plan.ton,
        duration_seconds=int(plan.duration.total_seconds()),
        crypto_asset=crypto_asset,
        crypto_amount=amount,
        payload=make_payment_payload(plan.code),
    )
    session.add(payment)
    await session.flush()
    return payment


async def send_stars_invoice(bot: Bot, chat_id: int, payment: Payment) -> None:
    title = _payment_title(payment)
    await bot.send_invoice(
        chat_id=chat_id,
        title=f"Подписка на {title}",
        description=f"Доступ в приватный чат и алерты листингов на {title}.",
        payload=payment.payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=payment.stars)],
    )


async def create_cryptobot_invoice(settings: Settings, payment: Payment) -> tuple[str, str]:
    if not settings.cryptobot_token:
        raise RuntimeError("CryptoBot token is not configured")

    asset = payment.crypto_asset or "TON"
    amount = payment.crypto_amount if payment.crypto_amount is not None else payment.ton
    result = await _cryptobot_request(
        settings,
        "createInvoice",
        {
            "asset": asset,
            "amount": f"{amount:g}",
            "description": f"Подписка на {_payment_title(payment)}",
            "payload": payment.payload,
            "expires_in": 3600,
        },
    )
    invoice_id = str(result["invoice_id"])
    pay_url = (
        result.get("pay_url")
        or result.get("bot_invoice_url")
        or result.get("mini_app_invoice_url")
        or result.get("web_app_invoice_url")
    )
    if not pay_url:
        raise RuntimeError("CryptoBot did not return invoice URL")
    return invoice_id, str(pay_url)


async def cryptobot_invoice_paid(settings: Settings, invoice_id: str) -> bool:
    if not settings.cryptobot_token:
        raise RuntimeError("CryptoBot token is not configured")

    result = await _cryptobot_request(
        settings,
        "getInvoices",
        {"invoice_ids": invoice_id},
    )
    items = result.get("items") or []
    if not items:
        return False
    return items[0].get("status") == "paid"


async def _cryptobot_request(settings: Settings, method: str, payload: dict) -> dict:
    url = f"{settings.cryptobot_api_url.rstrip('/')}/{method}"
    headers = {"Crypto-Pay-API-Token": settings.cryptobot_token or ""}
    async with aiohttp.ClientSession(headers=headers) as client:
        async with client.post(url, json=payload, timeout=20) as response:
            data = await response.json(content_type=None)
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or f"CryptoBot request failed: {method}")
    return data["result"]


def _payment_title(payment: Payment) -> str:
    if payment.duration_seconds:
        return format_duration(timedelta(seconds=payment.duration_seconds))
    return payment.plan_code
