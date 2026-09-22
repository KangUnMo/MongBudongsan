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
    scopes: tuple[str, ...] = ()

    def refresh(self, request: object) -> None:
        del request
        self.token = "fresh-access-token"
        self.expired = False

    def to_json(self) -> str:
        return json.dumps(
            {
                "token": self.token,
                "refresh_token": self.refresh_token,
                "expired": self.expired,
                "scopes": list(self.scopes),
            }
        )

    def has_scopes(self, scopes: tuple[str, ...]) -> bool:
        return set(scopes).issubset(self.scopes)


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
        scopes=tuple(decoded.get("scopes", ())),
    )


def test_reads_credentials_from_exact_keyring_service_and_refreshes_them(tmp_path: Path) -> None:
    required_scopes = (
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/gmail.send",
    )
    keyring = FakeKeyring(
        json.dumps(
            {
                "token": "expired-token",
                "refresh_token": "refresh",
                "expired": True,
                "scopes": list(required_scopes),
            }
        )
    )
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
            json.dumps(
                {
                    "token": "fresh-access-token",
                    "refresh_token": "refresh",
                    "expired": False,
                    "scopes": list(required_scopes),
                }
            ),
        )
    ]


def test_credentials_without_gmail_scope_require_explicit_reconsent(tmp_path: Path) -> None:
    keyring = FakeKeyring(
        json.dumps(
            {
                "token": "old-token",
                "refresh_token": "refresh",
                "scopes": [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive.file",
                ],
            }
        )
    )
    store = GoogleCredentialStore(
        tmp_path / "client-secret.json",
        keyring_backend=keyring,
        credential_decoder=_decode,
    )

    with pytest.raises(RuntimeError, match="re-consent"):
        store.load()


def test_missing_credentials_and_errors_redact_all_oauth_secrets(tmp_path: Path) -> None:
    store = GoogleCredentialStore(tmp_path / "client-secret.json", keyring_backend=FakeKeyring())

    with pytest.raises(RuntimeError, match="Google login"):
        store.load()

    unsafe = RuntimeError(
        "client_secret=client access_token=access refresh_token=refresh id_token=identity"
    )
    redacted = store.redact_error(unsafe)
    assert "=client" not in redacted
    assert "=access" not in redacted
    assert "=refresh" not in redacted
    assert "=identity" not in redacted
