from abc import ABC, abstractmethod


class WalletProviderAuthError(Exception):
    pass


class BaseWalletProviderAuthStrategy(ABC):
    """Contrat d'authentification fournisseur.

    Une implémentation réelle pourra plus tard :
    - obtenir un token,
    - rafraîchir un token,
    - signer une requête,
    - produire une API key,
    selon le protocole officiel du fournisseur.
    """

    @abstractmethod
    def get_headers(self):
        raise NotImplementedError


class NoAuthWalletProviderStrategy(
    BaseWalletProviderAuthStrategy
):
    """Stratégie utilisée lorsqu'aucune authentification
    sortante n'est nécessaire, notamment dans les tests.
    """

    def get_headers(self):
        return {}


def validate_auth_headers(headers):
    if not isinstance(headers, dict):
        raise WalletProviderAuthError(
            "Provider authentication headers must be a dictionary."
        )

    normalized = {}

    for key, value in headers.items():
        if not isinstance(key, str):
            raise WalletProviderAuthError(
                "Provider authentication header name must be a string."
            )

        key = key.strip()

        if not key:
            raise WalletProviderAuthError(
                "Provider authentication header name cannot be empty."
            )

        if not isinstance(value, str):
            raise WalletProviderAuthError(
                "Provider authentication header value must be a string."
            )

        if "\r" in value or "\n" in value:
            raise WalletProviderAuthError(
                "Provider authentication header contains invalid characters."
            )

        normalized[key] = value

    return normalized


from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock


@dataclass(frozen=True)
class WalletProviderAccessToken:
    access_token: str
    expires_at: datetime


class ExpiringBearerTokenAuthStrategy(
    BaseWalletProviderAuthStrategy
):
    """Stratégie Bearer générique avec cache local par process.

    token_loader est injecté et sera plus tard fourni par
    l'adaptateur Airtel/Moov réel.

    Aucun protocole OAuth spécifique n'est supposé ici.
    """

    def __init__(
        self,
        *,
        token_loader,
        refresh_skew_seconds=30,
        clock=None,
    ):
        if not callable(token_loader):
            raise WalletProviderAuthError(
                "Token loader must be callable."
            )

        if (
            isinstance(refresh_skew_seconds, bool)
            or not isinstance(
                refresh_skew_seconds,
                (int, float),
            )
            or refresh_skew_seconds < 0
            or refresh_skew_seconds > 300
        ):
            raise WalletProviderAuthError(
                "Invalid token refresh skew."
            )

        self.token_loader = token_loader
        self.refresh_skew_seconds = float(
            refresh_skew_seconds
        )

        self.clock = (
            clock
            if clock is not None
            else lambda: datetime.now(timezone.utc)
        )

        self._cached_token = None
        self._lock = Lock()

    def _normalize_token(self, value):
        if not isinstance(
            value,
            WalletProviderAccessToken,
        ):
            raise WalletProviderAuthError(
                "Token loader returned invalid token data."
            )

        token = value.access_token

        if not isinstance(token, str):
            raise WalletProviderAuthError(
                "Access token must be a string."
            )

        token = token.strip()

        if not token:
            raise WalletProviderAuthError(
                "Access token cannot be empty."
            )

        if "\r" in token or "\n" in token:
            raise WalletProviderAuthError(
                "Access token contains invalid characters."
            )

        expires_at = value.expires_at

        if (
            not isinstance(expires_at, datetime)
            or expires_at.tzinfo is None
        ):
            raise WalletProviderAuthError(
                "Token expiration must be timezone-aware."
            )

        return WalletProviderAccessToken(
            access_token=token,
            expires_at=expires_at,
        )

    def _is_usable(self, token, now):
        if token is None:
            return False

        refresh_at = (
            token.expires_at
            - timedelta(
                seconds=self.refresh_skew_seconds
            )
        )

        return now < refresh_at

    def _load_token(self):
        try:
            value = self.token_loader()
        except WalletProviderAuthError:
            raise
        except Exception as exc:
            raise WalletProviderAuthError(
                "Unable to obtain provider access token."
            ) from exc

        return self._normalize_token(value)

    def get_headers(self):
        now = self.clock()

        if self._is_usable(
            self._cached_token,
            now,
        ):
            return {
                "Authorization":
                    "Bearer "
                    + self._cached_token.access_token
            }

        with self._lock:
            now = self.clock()

            if not self._is_usable(
                self._cached_token,
                now,
            ):
                token = self._load_token()

                if not self._is_usable(
                    token,
                    now,
                ):
                    raise WalletProviderAuthError(
                        "Provider returned an already expired token."
                    )

                self._cached_token = token

            return {
                "Authorization":
                    "Bearer "
                    + self._cached_token.access_token
            }

    def clear_cached_token(self):
        with self._lock:
            self._cached_token = None
