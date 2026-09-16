import json
from abc import ABC, abstractmethod

from core.services.wallet_provider_service import (
    WalletProviderSignatureError,
    verify_mock_provider_signature,
)


class WalletProviderAdapterError(Exception):
    pass


class WalletProviderPayloadError(WalletProviderAdapterError):
    pass


class BaseWalletProviderAdapter(ABC):
    """Contrat commun pour tout fournisseur Mobile Money."""

    @abstractmethod
    def verify_and_parse(self, *, raw_body, headers):
        raise NotImplementedError


class MockWalletProviderAdapter(BaseWalletProviderAdapter):
    """Adaptateur de développement.

    Il ne représente ni Airtel Money ni Moov Money.
    """

    def __init__(self, *, secret):
        self.secret = secret

    def verify_and_parse(self, *, raw_body, headers):
        signature = headers.get(
            "X-DJINA-Signature",
            "",
        )

        try:
            verify_mock_provider_signature(
                raw_body=raw_body,
                signature=signature,
                secret=self.secret,
            )
        except WalletProviderSignatureError:
            raise

        try:
            decoded = raw_body.decode("utf-8")
            payload = json.loads(decoded)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise WalletProviderPayloadError(
                "Invalid JSON payload."
            ) from exc

        if not isinstance(payload, dict):
            raise WalletProviderPayloadError(
                "Webhook payload must be a JSON object."
            )

        return payload
