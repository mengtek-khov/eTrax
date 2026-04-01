from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class FernetTokenCipher:
    """Encryption adapter backed by cryptography Fernet."""

    def __init__(self, key: str) -> None:
        key_bytes = key.encode("ascii")
        self._fernet = Fernet(key_bytes)

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("ascii")

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("stored token cannot be decrypted with current secret key") from exc


def load_or_create_fernet_key(path: Path) -> str:
    """Load key from disk or create one for standalone mode."""

    if path.exists():
        return path.read_text(encoding="utf-8").strip()

    path.parent.mkdir(parents=True, exist_ok=True)
    key = FernetTokenCipher.generate_key()
    path.write_text(key, encoding="utf-8")
    return key
