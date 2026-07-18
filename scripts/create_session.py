from __future__ import annotations

import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession


def main() -> None:
    load_dotenv()
    api_id_raw = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id_raw or not api_hash:
        raise SystemExit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first.")

    with TelegramClient(StringSession(), int(api_id_raw), api_hash) as client:
        client.start()
        print("\nStringSession:")
        print(client.session.save())


if __name__ == "__main__":
    main()
