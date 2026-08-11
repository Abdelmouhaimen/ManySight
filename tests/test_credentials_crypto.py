import base64

import pytest

from server.services import credentials


KEY = base64.urlsafe_b64encode(b"a" * 32).decode()


def test_authenticated_encryption_round_trip_uses_random_nonce(monkeypatch):
    monkeypatch.setenv(credentials.KEY_ENV, KEY)
    first = credentials.encrypt({"password": "unit-secret"})
    second = credentials.encrypt({"password": "unit-secret"})
    assert first != second
    assert credentials.decrypt(first) == {"password": "unit-secret"}
    assert "unit-secret" not in first


def test_tampered_ciphertext_fails_safely(monkeypatch):
    monkeypatch.setenv(credentials.KEY_ENV, KEY)
    value = credentials.encrypt({"password": "unit-secret"})
    index = len(value) // 2
    tampered = value[:index] + ("A" if value[index] != "A" else "B") + value[index + 1:]
    with pytest.raises(credentials.CredentialDecryptionError):
        credentials.decrypt(tampered)


def test_missing_or_invalid_key_fails_closed(monkeypatch):
    monkeypatch.delenv(credentials.KEY_ENV, raising=False)
    with pytest.raises(credentials.CredentialConfigurationError):
        credentials.encrypt({"password": "unit-secret"})
    monkeypatch.setenv(credentials.KEY_ENV, base64.urlsafe_b64encode(b"short").decode())
    with pytest.raises(credentials.CredentialConfigurationError):
        credentials.encrypt({"password": "unit-secret"})
