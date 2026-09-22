from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from mybudongsan.integrations.google_auth import GoogleCredentialStore


@dataclass
class FakeCredentials:
    token: str
    refresh_token: str | None = None
    expired: bool = False

    def refresh(self, request: object) -> None:
        del request
        self.token = "fresh-access-token"
        self.expired = False

    def to_json(self) -> str:
        return json.dumps(
            {"token": self.token, "refresh_token": self.refresh_token, "expired": self.expired}
        )


class FakeKeyring:
    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self.calls: list[tuple[str, str, str]] = []

    def get_password(self, service: str, username: str) -> str | None:
        assert service == "mybudongsan-google"
        return self.value

    def set_password(self, service: str, username: str, password: str) -> None:
        self.calls.append((service, username, password))
        self.value = password


def _decode(payload: str, scopes: tuple[str, ...]) -> FakeCredentials:
    del scopes
    decoded = json.loads(payload)
    return FakeCredentials(
        token=decoded["token"],
        refresh_token=decoded.get("refresh_token"),
        expired=decoded.get("expired", False),
    )


def test_reads_credentials_from_exact_keyring_service_and_refreshes_them(tmp_path: Path) -> None:
    keyring = FakeKeyring(json.dumps({"token": "expired-token", "refresh_token": "refresh", "expired": True}))
    store = GoogleCredentialStore(
        tmp_path / "client-secret.json",
        keyring_backend=keyring,
        credential_decoder=_decode,
        request_factory=object,
    )

    credentials = store.load()

    assert credentials.token == "fresh-access-token"
    assert keyring.calls == [
        (
            "mybudongsan-google",
            "default",
            json.dumps({"token": "fresh-access-token", "refresh_token": "refresh", "expired": False}),
        )
    ]


def test_missing_credentials_and_errors_redact_all_oauth_secrets(tmp_path: Path) -> None:
    store = GoogleCredentialStore(tmp_path / "client-secret.json", keyring_backend=FakeKeyring())

    with pytest.raises(RuntimeError, match="Google login"):
        store.load()

    unsafe = RuntimeError(
        "client_secret=client access_token=access refresh_token=refresh id_token=identity"
    )
    assert "client" not in store.redact_error(unsafe)
    assert "access" not in store.redact_error(unsafe)
    assert "refresh" not in store.redact_error(unsafe)
    assert "identity" not in store.redact_error(unsafe)
