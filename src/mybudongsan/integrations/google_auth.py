from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import keyring
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]

GOOGLE_KEYRING_SERVICE = "mybudongsan-google"
_KEYRING_USERNAME = "default"
GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
)
_SECRET_PATTERN = re.compile(
    r"(?:client_secret|access_token|refresh_token|id_token|token)"
    r"(?:\s*[:=]\s*|\"\s*:\s*\")[^,\s\}\]\"]+",
    flags=re.IGNORECASE,
)


class GoogleCredentialStore:
    """Load user OAuth credentials exclusively from the operating-system keychain."""

    def __init__(
        self,
        client_secret_path: Path | None = None,
        *,
        keyring_backend: Any = keyring,
        credential_decoder: Callable[[str, tuple[str, ...]], Any] | None = None,
        request_factory: Callable[[], Any] = Request,
        flow_factory: Callable[[str, tuple[str, ...]], Any] | None = None,
    ) -> None:
        self._client_secret_path = client_secret_path
        self._keyring = keyring_backend
        self._credential_decoder = credential_decoder or _decode_credentials
        self._request_factory = request_factory
        self._flow_factory = flow_factory or _create_flow

    def load(self) -> Any:
        """Return stored credentials, refreshing an expired refreshable token once."""
        try:
            serialized = self._keyring.get_password(GOOGLE_KEYRING_SERVICE, _KEYRING_USERNAME)
            if not serialized:
                raise RuntimeError("Google login is required; run `mybudongsan google login` first")
            credentials = self._credential_decoder(serialized, GOOGLE_SCOPES)
            if bool(getattr(credentials, "expired", False)):
                if not getattr(credentials, "refresh_token", None):
                    raise RuntimeError("Google login is required because the stored session cannot refresh")
                credentials.refresh(self._request_factory())
                self.save(credentials)
            return credentials
        except RuntimeError as error:
            raise RuntimeError(self.redact_error(error)) from None
        except Exception as error:  # noqa: BLE001 - redact every backend exception before CLI output
            raise RuntimeError(self.redact_error(error)) from None

    def save(self, credentials: Any) -> None:
        try:
            serialized = credentials.to_json()
            self._keyring.set_password(GOOGLE_KEYRING_SERVICE, _KEYRING_USERNAME, serialized)
        except Exception as error:  # noqa: BLE001 - redact every keychain exception before CLI output
            raise RuntimeError(self.redact_error(error)) from None

    def login(self) -> None:
        """Run local-browser consent only when the explicit login CLI command invokes it."""
        if self._client_secret_path is None:
            raise RuntimeError("a local OAuth client-secret path is required for Google login")
        try:
            flow = self._flow_factory(str(self._client_secret_path), GOOGLE_SCOPES)
            self.save(flow.run_local_server(port=0))
        except RuntimeError as error:
            raise RuntimeError(self.redact_error(error)) from None
        except Exception as error:  # noqa: BLE001 - redact every OAuth exception before CLI output
            raise RuntimeError(self.redact_error(error)) from None

    @staticmethod
    def redact_error(error: BaseException | str) -> str:
        """Remove OAuth secrets before an integration error can reach CLI output."""
        message = str(error)
        return _SECRET_PATTERN.sub("[credential redacted]", message)


def _decode_credentials(serialized: str, scopes: tuple[str, ...]) -> Credentials:
    return cast(
        Credentials,
        Credentials.from_authorized_user_info(json.loads(serialized), scopes=scopes),  # type: ignore[no-untyped-call]
    )


def _create_flow(client_secret_path: str, scopes: tuple[str, ...]) -> InstalledAppFlow:
    return InstalledAppFlow.from_client_secrets_file(client_secret_path, scopes=scopes)
