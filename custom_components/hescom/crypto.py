"""Decryption helpers for HESCOM API responses.

The HESCOM frontend receives some endpoints as a JSON wrapper of the form
``{"_cdata": "<payload>"}``.  The payload is a plain string with this layout:

    saltHex (64 chars) + ivHex (32 chars) + base64(ciphertext)

The ciphertext is AES-256-CBC with PKCS7 padding.  The symmetric key is derived
with PBKDF2-HMAC-SHA1 over the shared passphrase bubble, 1989 iterations,
32 bytes output.

Nothing here is used for encryption: this integration never sends request
bodies that need wrapping, because the API accepts plain JSON.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: Shared secret used for both directions of the wire format.
PASSPHRASE = "b57ea4714sd2d6ahi7896e8"

SALT_HEX_LEN = 64
IV_HEX_LEN = 32
HEADER_HEX_LEN = SALT_HEX_LEN + IV_HEX_LEN
ITERATIONS = 1989
KEY_LEN = 32
AES_BLOCK_SIZE = 16


class DecryptionError(Exception):
    """Raised when a payload cannot be decrypted."""


def _derive_key(salt: bytes, passphrase: str = PASSPHRASE) -> bytes:
    """PBKDF2-HMAC-SHA1, matching the frontend crypto service."""
    return hashlib.pbkdf2_hmac(
        "sha1", passphrase.encode("utf-8"), salt, ITERATIONS, KEY_LEN
    )


def _strip_pkcs7(data: bytes) -> bytes:
    """Remove PKCS7 padding, tolerating a missing/foreign padding scheme."""
    if not data:
        return data
    pad = data[-1]
    if pad < 1 or pad > AES_BLOCK_SIZE or data[-pad:] != bytes([pad]) * pad:
        return data
    return data[:-pad]


def decrypt_payload(payload: str, passphrase: str = PASSPHRASE) -> str:
    """Decrypt a raw ``_cdata`` string and return the plaintext."""
    text = payload.strip()
    if len(text) <= HEADER_HEX_LEN:
        raise DecryptionError("payload too short to contain salt/iv header")

    salt_hex = text[:SALT_HEX_LEN]
    iv_hex = text[SALT_HEX_LEN:HEADER_HEX_LEN]
    body = text[HEADER_HEX_LEN:]

    try:
        salt = bytes.fromhex(salt_hex)
        iv = bytes.fromhex(iv_hex)
    except ValueError as err:
        raise DecryptionError(f"invalid salt/iv header: {err}") from err

    if len(salt) != SALT_HEX_LEN // 2 or len(iv) != IV_HEX_LEN // 2:
        raise DecryptionError("invalid salt/iv length")

    try:
        ciphertext = base64.b64decode(body, validate=False)
    except (binascii.Error, ValueError) as err:
        raise DecryptionError(f"invalid base64 body: {err}") from err

    if not ciphertext or len(ciphertext) % AES_BLOCK_SIZE:
        raise DecryptionError("ciphertext is not a whole number of AES blocks")

    # Imported lazily so that a missing optional dependency degrades to a
    # readable warning instead of preventing the integration from loading.
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as err:  # pragma: no cover - HA core always ships it
        raise DecryptionError("cryptography package is unavailable") from err

    key = _derive_key(salt, passphrase)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    try:
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    except ValueError as err:
        raise DecryptionError(f"AES-CBC decryption failed: {err}") from err

    try:
        return _strip_pkcs7(plaintext).decode("utf-8")
    except UnicodeDecodeError as err:
        raise DecryptionError(f"plaintext is not UTF-8: {err}") from err


def decrypt_json(payload: str, passphrase: str = PASSPHRASE) -> Any:
    """Decrypt a payload whose plaintext is JSON."""
    plaintext = decrypt_payload(payload, passphrase)
    try:
        return json.loads(plaintext)
    except json.JSONDecodeError as err:
        raise DecryptionError(f"plaintext is not valid JSON: {err}") from err


def unwrap(value: Any, passphrase: str = PASSPHRASE) -> Any:
    """Return ``value`` decrypted if it is a ``_cdata`` wrapper.

    Accepts the wrapper in the shapes the API actually uses:

    * ``{"_cdata": "<payload>"}``
    * ``{"_cdata": {"_cdata": ...}}`` (rare double wrap)
    * a bare ``_cdata`` string
    * anything else, returned unchanged

    On failure the original value is returned so callers do not lose data.
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{"):
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError:
                return value
        else:
            return value

    for _ in range(3):
        if not isinstance(value, dict) or "_cdata" not in value:
            break
        payload = value["_cdata"]
        if not isinstance(payload, str):
            break
        try:
            if payload.strip().startswith("{"):
                value = json.loads(payload)
            else:
                value = decrypt_json(payload, passphrase)
        except (DecryptionError, json.JSONDecodeError) as err:
            _LOGGER.debug("Unable to decrypt _cdata payload: %s", err)
            return value
    return value
