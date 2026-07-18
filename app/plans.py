from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.settings import get_setting, set_setting


@dataclass(frozen=True)
class Plan:
    code: str
    title: str
    duration: timedelta
    ton: float
    usdt: float
    stars: int


PLANS: dict[str, Plan] = {
    "6h": Plan("6h", "6 часов", timedelta(hours=6), 1, 1, 250),
    "1d": Plan("1d", "1 день", timedelta(days=1), 3, 3, 750),
    "3d": Plan("3d", "3 дня", timedelta(days=3), 5, 5, 1250),
    "6d": Plan("6d", "6 дней", timedelta(days=6), 8, 8, 2000),
    "12d": Plan("12d", "12 дней", timedelta(days=12), 10, 10, 2500),
    "30d": Plan("30d", "30 дней", timedelta(days=30), 20, 20, 5000),
}


async def get_plan(session: AsyncSession, code: str) -> Plan:
    default = PLANS[code]
    seconds = int(await get_setting(session, _key(code, "duration_seconds"), str(int(default.duration.total_seconds()))) or "0")
    stars = int(await get_setting(session, _key(code, "stars"), str(default.stars)) or "0")
    ton = _float_setting(await get_setting(session, _key(code, "ton"), str(default.ton)), default.ton)
    usdt = _float_setting(await get_setting(session, _key(code, "usdt"), str(default.usdt)), default.usdt)
    duration = timedelta(seconds=max(seconds, 60))
    return Plan(code, format_duration(duration), duration, ton, usdt, stars)


async def get_plans(session: AsyncSession) -> list[Plan]:
    return [await get_plan(session, code) for code in PLANS]


async def set_plan_field(session: AsyncSession, code: str, field: str, value: str) -> None:
    if code not in PLANS:
        raise ValueError("Unknown plan")
    if field not in {"duration_seconds", "stars", "ton", "usdt"}:
        raise ValueError("Unknown plan field")
    await set_setting(session, _key(code, field), value)


def crypto_amount(plan: Plan, asset: str) -> float:
    asset = asset.upper()
    if asset == "TON":
        return plan.ton
    if asset == "USDT":
        return plan.usdt
    raise ValueError("Unsupported crypto asset")


def parse_duration(value: str) -> timedelta:
    raw = value.strip().lower().replace(",", ".")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([a-zа-я]+)?", raw)
    if not match:
        raise ValueError("Bad duration")
    amount = float(match.group(1))
    unit = match.group(2) or "h"
    if unit in {"m", "min", "mins", "minute", "minutes", "мин", "минута", "минут", "минуты"}:
        seconds = amount * 60
    elif unit in {"h", "hr", "hrs", "hour", "hours", "ч", "час", "часа", "часов"}:
        seconds = amount * 3600
    elif unit in {"d", "day", "days", "д", "день", "дня", "дней"}:
        seconds = amount * 86400
    else:
        raise ValueError("Bad duration unit")
    if seconds < 60 or seconds > 366 * 86400:
        raise ValueError("Duration out of range")
    return timedelta(seconds=int(seconds))


def format_duration(duration: timedelta) -> str:
    seconds = int(duration.total_seconds())
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} {_plural_ru(days, 'день', 'дня', 'дней')}"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} {_plural_ru(hours, 'час', 'часа', 'часов')}"
    minutes = max(1, seconds // 60)
    return f"{minutes} {_plural_ru(minutes, 'минута', 'минуты', 'минут')}"


def _key(code: str, field: str) -> str:
    return f"plan:{code}:{field}"


def _float_setting(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _plural_ru(number: int, one: str, few: str, many: str) -> str:
    number = abs(number)
    if number % 100 in range(11, 15):
        return many
    if number % 10 == 1:
        return one
    if number % 10 in range(2, 5):
        return few
    return many
