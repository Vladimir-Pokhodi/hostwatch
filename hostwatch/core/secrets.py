"""AES-GCM encryption for hoster credentials stored in the database.

Blob layout: 12-byte random nonce followed by the ciphertext with the GCM tag.
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_SIZE = 32
NONCE_SIZE = 12


class InvalidKeyError(ValueError):
    """HOSTWATCH_KEY is not base64 of exactly 32 bytes."""


class DecryptionError(Exception):
    """The blob was encrypted with another key or has been tampered with."""


def generate_key() -> str:
    return base64.b64encode(os.urandom(KEY_SIZE)).decode("ascii")


def load_key(encoded: str) -> bytes:
    try:
        key = base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise InvalidKeyError("HOSTWATCH_KEY is not valid base64") from exc
    if len(key) != KEY_SIZE:
        raise InvalidKeyError(f"HOSTWATCH_KEY must decode to {KEY_SIZE} bytes, got {len(key)}")
    return key


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    nonce = os.urandom(NONCE_SIZE)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, None)


def decrypt(blob: bytes, key: bytes) -> bytes:
    nonce, ciphertext = blob[:NONCE_SIZE], blob[NONCE_SIZE:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except InvalidTag as exc:
        raise DecryptionError("cannot decrypt: wrong HOSTWATCH_KEY or corrupted data") from exc
