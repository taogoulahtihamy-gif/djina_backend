from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests


class WalletProviderHTTPError(Exception):
    """Erreur générique de transport vers un fournisseur."""
    pass


class WalletProviderHTTPConfigurationError(WalletProviderHTTPError):
    pass


class WalletProviderHTTPTimeoutError(WalletProviderHTTPError):
    pass


class WalletProviderHTTPNetworkError(WalletProviderHTTPError):
    pass


class WalletProviderHTTPStatusError(WalletProviderHTTPError):
    def __init__(self, *, status_code):
        self.status_code = status_code

        super().__init__(
            f"Provider returned HTTP {status_code}."
        )


class WalletProviderHTTPPayloadError(WalletProviderHTTPError):
    pass


@dataclass(frozen=True)
class WalletProviderHTTPResponse:
    status_code: int
    data: object
    headers: dict


class WalletProviderHTTPClient:
    """Transport HTTP JSON commun aux fournisseurs Mobile Money.

    Cette classe ne connaît aucun protocole Airtel/Moov.
    Elle ne connaît que HTTP.

    Aucun retry automatique n'est effectué ici :
    les opérations financières nécessitent une stratégie
    d'idempotence explicite au niveau fournisseur.
    """

    ALLOWED_METHODS = {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }

    def __init__(
        self,
        *,
        base_url,
        timeout_seconds,
        session=None,
    ):
        self.base_url = self._normalize_base_url(
            base_url
        )

        self.timeout_seconds = self._normalize_timeout(
            timeout_seconds
        )

        self.session = (
            session
            if session is not None
            else requests.Session()
        )

    @staticmethod
    def _normalize_base_url(value):
        if not isinstance(value, str):
            raise WalletProviderHTTPConfigurationError(
                "Provider base URL must be a string."
            )

        value = value.strip()

        if not value:
            raise WalletProviderHTTPConfigurationError(
                "Provider base URL is required."
            )

        parsed = urlsplit(value)

        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
        ):
            raise WalletProviderHTTPConfigurationError(
                "Provider base URL is invalid."
            )

        if parsed.username or parsed.password:
            raise WalletProviderHTTPConfigurationError(
                "Credentials must not be embedded in provider URL."
            )

        return value.rstrip("/") + "/"

    @staticmethod
    def _normalize_timeout(value):
        if isinstance(value, bool):
            raise WalletProviderHTTPConfigurationError(
                "Provider timeout is invalid."
            )

        try:
            timeout = float(value)
        except (TypeError, ValueError) as exc:
            raise WalletProviderHTTPConfigurationError(
                "Provider timeout is invalid."
            ) from exc

        if timeout <= 0 or timeout > 60:
            raise WalletProviderHTTPConfigurationError(
                "Provider timeout must be between 0 and 60 seconds."
            )

        return timeout

    def _build_url(self, path):
        if not isinstance(path, str):
            raise WalletProviderHTTPConfigurationError(
                "Provider path must be a string."
            )

        path = path.strip()

        if not path:
            raise WalletProviderHTTPConfigurationError(
                "Provider path is required."
            )

        parsed_path = urlsplit(path)

        # Un adaptateur ne doit jamais pouvoir remplacer
        # dynamiquement le domaine configuré.
        if parsed_path.scheme or parsed_path.netloc:
            raise WalletProviderHTTPConfigurationError(
                "Provider path must be relative."
            )

        url = urljoin(
            self.base_url,
            path.lstrip("/"),
        )

        base = urlsplit(self.base_url)
        final = urlsplit(url)

        if (
            final.scheme != base.scheme
            or final.netloc != base.netloc
        ):
            raise WalletProviderHTTPConfigurationError(
                "Provider URL escaped configured origin."
            )

        return url

    def request_json(
        self,
        *,
        method,
        path,
        headers=None,
        json_body=None,
        params=None,
    ):
        if not isinstance(method, str):
            raise WalletProviderHTTPConfigurationError(
                "HTTP method is invalid."
            )

        method = method.strip().upper()

        if method not in self.ALLOWED_METHODS:
            raise WalletProviderHTTPConfigurationError(
                "HTTP method is not allowed."
            )

        url = self._build_url(path)

        request_headers = {
            "Accept": "application/json",
        }

        if headers is not None:
            if not isinstance(headers, dict):
                raise WalletProviderHTTPConfigurationError(
                    "HTTP headers must be a dictionary."
                )

            request_headers.update(headers)

        try:
            response = self.session.request(
                method=method,
                url=url,
                headers=request_headers,
                json=json_body,
                params=params,
                timeout=self.timeout_seconds,
            )

        except requests.Timeout as exc:
            raise WalletProviderHTTPTimeoutError(
                "Provider request timed out."
            ) from exc

        except requests.RequestException as exc:
            raise WalletProviderHTTPNetworkError(
                "Provider network request failed."
            ) from exc

        if not 200 <= response.status_code < 300:
            raise WalletProviderHTTPStatusError(
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise WalletProviderHTTPPayloadError(
                "Provider returned invalid JSON."
            ) from exc

        return WalletProviderHTTPResponse(
            status_code=response.status_code,
            data=data,
            headers=dict(response.headers),
        )


def build_wallet_provider_http_client(
    config,
    *,
    session=None,
):
    """Construit le transport depuis WalletProviderConfig."""

    base_url = getattr(
        config,
        "base_url",
        "",
    )

    timeout_seconds = getattr(
        config,
        "timeout_seconds",
        None,
    )

    return WalletProviderHTTPClient(
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        session=session,
    )
