from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SessionCipher:
    def __init__(self, secret: str) -> None:
        self._fernet = Fernet(secret.encode())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Cannot decrypt stored session. Check SESSION_SECRET.") from exc
