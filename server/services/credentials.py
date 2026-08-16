"""Authenticated encryption for source credentials.

The key is deployment configuration, never database state.  A missing or malformed
key fails closed: ManySight will still serve safe metadata but cannot store or resolve
managed credentials until the operator configures one.
"""
import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_ENV = "MANYSIGHT_CREDENTIAL_KEY"
# The associated data binds a ciphertext to this product's credential store, so
# it is part of the envelope, not a label. It changed with the project name, and
# AES-GCM authenticates it: ciphertext written under the previous associated
# data cannot be decrypted. The prefix is bumped alongside it so that shows up as
# an explicit unsupported-version error rather than an opaque decryption failure,
# and the operator is told to save the credential again.
AAD = b"manysight-source-credentials-v1"
PREFIX = "v2:"


class CredentialConfigurationError(RuntimeError):
    pass


class CredentialDecryptionError(RuntimeError):
    pass


def key_configured() -> bool:
    try:
        _key()
        return True
    except CredentialConfigurationError:
        return False


def _key() -> bytes:
    value = os.environ.get(KEY_ENV, "").strip()
    if not value:
        raise CredentialConfigurationError(
            f"{KEY_ENV} is required for managed source credentials"
        )
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise CredentialConfigurationError(
            f"{KEY_ENV} must be URL-safe base64"
        ) from exc
    if len(decoded) != 32:
        raise CredentialConfigurationError(
            f"{KEY_ENV} must decode to exactly 32 bytes"
        )
    return decoded


def encrypt(payload: dict) -> str:
    nonce = os.urandom(12)
    plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ciphertext = AESGCM(_key()).encrypt(nonce, plaintext, AAD)
    encoded = base64.urlsafe_b64encode(nonce + ciphertext).decode().rstrip("=")
    return PREFIX + encoded


def decrypt(value: str) -> dict:
    if not value.startswith(PREFIX):
        raise CredentialDecryptionError(
            "this credential was stored by an earlier pre-release build and cannot be read; "
            "save the source's credentials again")
    try:
        encoded = value[len(PREFIX):]
        raw = base64.urlsafe_b64decode((encoded + "=" * (-len(encoded) % 4)).encode())
        plaintext = AESGCM(_key()).decrypt(raw[:12], raw[12:], AAD)
        result = json.loads(plaintext)
        if not isinstance(result, dict):
            raise ValueError("credential payload is not an object")
        return result
    except CredentialConfigurationError:
        raise
    except Exception as exc:
        raise CredentialDecryptionError("managed credentials could not be decrypted") from exc
