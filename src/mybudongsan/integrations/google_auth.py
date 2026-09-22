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
    "https://www.googleapis.com/auth/gmail.send",
)
_SENSITIVE_FIELD = (
    r"client_secret|access_token|refresh_token|id_token|client_token|"
    r"playmcp_token|api[_-]?key|authorization|password"
)
_SECRET_ASSIGNMENT = re.compile(
    rf"(?P<prefix>['\"]?(?:{_SENSITIVE_FIELD})['\"]?\s*(?:\?=|[:=]|\s+)\s*)"
    r"(?P<value>'[^']*'|\"[^\"]*\"|[^\s,}\]&]+)",
    flags=re.IGNORECASE,
)
_BEARER_TOKEN = re.compile(r"\b(?:bearer|token)\s+(?P<value>[^\s,}\]&]+)", re.IGNORECASE)


class GoogleIntegrationError(RuntimeError):
    """A Google boundary failure whose message has been stripped of credential values."""


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
                raise GoogleIntegrationError(
                    "Google login is required; run `mybudongsan google login` first"
                )
            if not set(GOOGLE_SCOPES).issubset(_stored_scopes(serialized)):
                raise GoogleIntegrationError(
                    "Google re-consent is required for Gmail send; "
                    "run `mybudongsan google login` again"
                )
            credentials = self._credential_decoder(serialized, GOOGLE_SCOPES)
            if bool(getattr(credentials, "expired", False)):
                if not getattr(credentials, "refresh_token", None):
                    raise GoogleIntegrationError(
                        "Google login is required because the stored session cannot refresh"
                    )
                credentials.refresh(self._request_factory())
                self.save(credentials)
            return credentials
        except Exception as error:  # noqa: BLE001 - a credential boundary must never leak values
            raise sanitized_google_error(error) from None

    def save(self, credentials: Any) -> None:
        try:
            serialized = credentials.to_json()
            self._keyring.set_password(GOOGLE_KEYRING_SERVICE, _KEYRING_USERNAME, serialized)
        except Exception as error:  # noqa: BLE001 - a keychain boundary must never leak values
            raise sanitized_google_error(error) from None

    def login(self) -> None:
        """Run local-browser consent only when the explicit login CLI command invokes it."""
        if self._client_secret_path is None:
            raise GoogleIntegrationError("a local OAuth client-secret path is required for Google login")
        try:
            flow = self._flow_factory(str(self._client_secret_path), GOOGLE_SCOPES)
            self.save(flow.run_local_server(port=0))
        except Exception as error:  # noqa: BLE001 - an OAuth boundary must never leak values
            raise sanitized_google_error(error) from None

    @staticmethod
    def redact_error(error: BaseException | str) -> str:
        """Remove OAuth secrets before an integration error can reach CLI output."""
        return redact_google_text(str(error))


def redact_google_text(message: str) -> str:
    """Scrub JSON, dict, key/value, query-string and bearer credential renderings."""
    message = _SECRET_ASSIGNMENT.sub(r"\g<prefix>[credential redacted]", message)
    return _BEARER_TOKEN.sub("Bearer [credential redacted]", message)


def sanitized_google_error(error: BaseException | str) -> GoogleIntegrationError:
    """Build a fresh exception without an original cause, context, or secret-bearing repr."""
    return GoogleIntegrationError(redact_google_text(str(error)))


def _decode_credentials(serialized: str, scopes: tuple[str, ...]) -> Credentials:
    return cast(
        Credentials,
        Credentials.from_authorized_user_info(json.loads(serialized), scopes=scopes),  # type: ignore[no-untyped-call]
    )


def _stored_scopes(serialized: str) -> set[str]:
    payload = json.loads(serialized)
    if not isinstance(payload, dict):
        return set()
    scopes = payload.get("scopes", ())
    if isinstance(scopes, str):
        return set(scopes.split())
    if isinstance(scopes, list):
        return {scope for scope in scopes if isinstance(scope, str)}
    return set()


def _create_flow(client_secret_path: str, scopes: tuple[str, ...]) -> InstalledAppFlow:
    return InstalledAppFlow.from_client_secrets_file(client_secret_path, scopes=scopes)
