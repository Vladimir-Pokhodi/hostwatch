import pytest

from hostwatch.core.secrets import (
    DecryptionError,
    InvalidKeyError,
    decrypt,
    encrypt,
    generate_key,
    load_key,
)


def test_round_trip() -> None:
    key = load_key(generate_key())
    plaintext = b'{"api_key": "example"}'

    blob = encrypt(plaintext, key)

    assert plaintext not in blob
    assert decrypt(blob, key) == plaintext


def test_same_plaintext_gives_different_blobs() -> None:
    key = load_key(generate_key())

    assert encrypt(b"x", key) != encrypt(b"x", key)


def test_wrong_key_fails() -> None:
    blob = encrypt(b"secret", load_key(generate_key()))

    with pytest.raises(DecryptionError):
        decrypt(blob, load_key(generate_key()))


def test_tampered_blob_fails() -> None:
    key = load_key(generate_key())
    blob = bytearray(encrypt(b"secret", key))
    blob[-1] ^= 1

    with pytest.raises(DecryptionError):
        decrypt(bytes(blob), key)


@pytest.mark.parametrize("encoded", ["not base64!", "c2hvcnQ="])
def test_bad_key_rejected(encoded: str) -> None:
    with pytest.raises(InvalidKeyError):
        load_key(encoded)
