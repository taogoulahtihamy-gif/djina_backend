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
