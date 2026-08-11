"""Fernet symmetric encryption helpers for sensitive credential storage."""
from __future__ import annotations

import logging
import os

from cryptography.fernet import Fernet, InvalidToken

_KEY_FILENAME = ".pf_secret_key"


def _key_path(filename: str = _KEY_FILENAME) -> str:
    # Prefer to co-locate the key with the DB (persistent volume in Docker).
    # PF_DB_PATH is set to /data/photoframe.db in Docker; fall back to repo root.
    db_path = os.environ.get("PF_DB_PATH", "")
    if db_path:
        return os.path.join(os.path.dirname(db_path), filename)
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(repo_root, filename)


def load_or_create_key(filename: str = _KEY_FILENAME) -> bytes:
    """Load Fernet key from file, generating and saving it on first run."""
    path = _key_path(filename)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read().strip()
    key = Fernet.generate_key()
    with open(path, "wb") as f:
        f.write(key)
    logging.warning(
        "[Encryption] Generated new secret key at %s. Back this file up — "
        "losing it means re-authenticating all sources.",
        path,
    )
    return key


def encrypt(plaintext: str, key: bytes) -> str:
    """Encrypt a UTF-8 string. Returns base64 ciphertext string."""
    return Fernet(key).encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str, key: bytes) -> str:
    """Decrypt a ciphertext string. Raises InvalidToken if key is wrong or data is corrupt."""
    return Fernet(key).decrypt(ciphertext.encode()).decode()


def encrypt_json(data: dict, key: bytes) -> str:
    """Convenience: JSON-encode a dict then encrypt."""
    import json

    return encrypt(json.dumps(data), key)


def decrypt_json(ciphertext: str, key: bytes) -> dict:
    """Convenience: decrypt then JSON-decode."""
    import json

    return json.loads(decrypt(ciphertext, key))


__all__ = [
    "InvalidToken",
    "load_or_create_key",
    "encrypt",
    "decrypt",
    "encrypt_json",
    "decrypt_json",
]
