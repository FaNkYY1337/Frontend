from __future__ import annotations

from dataclasses import dataclass

from telethon import TelegramClient
from telethon.errors import PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError
from telethon.sessions import StringSession

from app.config import Settings


@dataclass(frozen=True)
class CodeRequest:
    temp_session: str
    phone_code_hash: str


@dataclass(frozen=True)
class LoginResult:
    session_string: str
    needs_password: bool = False


async def request_login_code(settings: Settings, phone: str) -> CodeRequest:
    client = TelegramClient(StringSession(), settings.telegram_api_id, settings.telegram_api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        return CodeRequest(
            temp_session=client.session.save(),
            phone_code_hash=sent.phone_code_hash,
        )
    finally:
        await client.disconnect()


async def sign_in_with_code(
    settings: Settings,
    temp_session: str,
    phone: str,
    code: str,
    phone_code_hash: str,
) -> LoginResult:
    client = TelegramClient(
        StringSession(temp_session),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.connect()
    try:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            return LoginResult(session_string=client.session.save(), needs_password=True)
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            raise
        return LoginResult(session_string=client.session.save())
    finally:
        await client.disconnect()


async def sign_in_with_password(
    settings: Settings,
    temp_session: str,
    password: str,
) -> str:
    client = TelegramClient(
        StringSession(temp_session),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.connect()
    try:
        await client.sign_in(password=password)
        return client.session.save()
    finally:
        await client.disconnect()
