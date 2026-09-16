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


from core.services.wallet_provider_outbound import (
    BaseWalletProviderOutgoingAdapter,
    WalletProviderHTTPRequestSpec,
    WalletProviderTopUpInitiationResult,
)


class MockWalletProviderOutgoingAdapter(
    BaseWalletProviderOutgoingAdapter
):
    """Protocole sortant de test uniquement.

    Il ne représente ni Airtel Money ni Moov Money.
    """

    def build_topup_request(
        self,
        command,
    ):
        return WalletProviderHTTPRequestSpec(
            method="POST",
            path="topups",
            headers={
                "X-DJINA-Idempotency-Key":
                    command.idempotency_key,
                "X-DJINA-Correlation-ID":
                    command.correlation_id,
            },
            json_body={
                "topup_id": command.topup_id,
                "amount": str(command.amount),
                "currency": command.currency,
                "phone": command.phone,
            },
        )

    def parse_topup_response(
        self,
        *,
        response,
        command,
    ):
        data = response.data

        if not isinstance(data, dict):
            raise ValueError(
                "Mock provider response must be an object."
            )

        provider_reference = data.get(
            "provider_reference"
        )
        provider_status = data.get(
            "status"
        )

        if (
            not isinstance(provider_reference, str)
            or not provider_reference.strip()
        ):
            raise ValueError(
                "Missing mock provider reference."
            )

        if (
            not isinstance(provider_status, str)
            or not provider_status.strip()
        ):
            raise ValueError(
                "Missing mock provider status."
            )

        return WalletProviderTopUpInitiationResult(
            provider_reference=
                provider_reference.strip(),
            provider_status=
                provider_status.strip(),
            raw_data=data,
        )
