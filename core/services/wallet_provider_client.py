from core.services.wallet_provider_auth import (
    BaseWalletProviderAuthStrategy,
    WalletProviderAuthError,
    validate_auth_headers,
)
from core.services.wallet_provider_http import (
    WalletProviderHTTPClient,
)


class WalletProviderClientError(Exception):
    pass


class WalletProviderClientConfigurationError(
    WalletProviderClientError
):
    pass


class WalletProviderAPIClient:
    """Client fournisseur générique.

    Il compose :
    - une stratégie d'authentification,
    - le transport HTTP commun.

    Il ne connaît aucun endpoint Airtel/Moov.
    """

    def __init__(
        self,
        *,
        http_client,
        auth_strategy,
    ):
        if not isinstance(
            http_client,
            WalletProviderHTTPClient,
        ):
            raise WalletProviderClientConfigurationError(
                "Invalid provider HTTP client."
            )

        if not isinstance(
            auth_strategy,
            BaseWalletProviderAuthStrategy,
        ):
            raise WalletProviderClientConfigurationError(
                "Invalid provider authentication strategy."
            )

        self.http_client = http_client
        self.auth_strategy = auth_strategy

    def request_json(
        self,
        *,
        method,
        path,
        headers=None,
        json_body=None,
        params=None,
    ):
        if headers is None:
            request_headers = {}
        elif isinstance(headers, dict):
            request_headers = dict(headers)
        else:
            raise WalletProviderClientConfigurationError(
                "Provider request headers must be a dictionary."
            )

        try:
            auth_headers = validate_auth_headers(
                self.auth_strategy.get_headers()
            )
        except WalletProviderAuthError:
            raise
        except Exception as exc:
            raise WalletProviderAuthError(
                "Provider authentication failed."
            ) from exc

        # L'authentification a priorité sur les headers fournis
        # par l'appelant. Un adaptateur métier ne peut donc pas
        # remplacer accidentellement Authorization/API-Key.
        request_headers.update(auth_headers)

        return self.http_client.request_json(
            method=method,
            path=path,
            headers=request_headers,
            json_body=json_body,
            params=params,
        )
