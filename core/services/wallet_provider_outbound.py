from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import uuid4

from core.services.wallet_provider_auth import (
    WalletProviderAuthError,
)
from core.services.wallet_provider_client import (
    WalletProviderAPIClient,
)
from core.services.wallet_provider_http import (
    WalletProviderHTTPNetworkError,
    WalletProviderHTTPPayloadError,
    WalletProviderHTTPStatusError,
    WalletProviderHTTPTimeoutError,
)
from core.services.wallet_service import (
    InvalidWalletAmountError,
    _normalize_amount,
)


class WalletProviderOutboundError(Exception):
    pass


class WalletProviderOutboundValidationError(
    WalletProviderOutboundError
):
    pass


class WalletProviderOutboundAuthenticationError(
    WalletProviderOutboundError
):
    pass


class WalletProviderOutboundTimeoutError(
    WalletProviderOutboundError
):
    pass


class WalletProviderOutboundUnavailableError(
    WalletProviderOutboundError
):
    pass


class WalletProviderOutboundRejectedError(
    WalletProviderOutboundError
):
    def __init__(self, *, status_code):
        self.status_code = status_code

        super().__init__(
            f"Provider rejected request with HTTP {status_code}."
        )


class WalletProviderOutboundProtocolError(
    WalletProviderOutboundError
):
    pass


@dataclass(frozen=True)
class WalletProviderTopUpCommand:
    topup_id: int
    amount: object
    currency: str
    phone: str
    idempotency_key: str
    correlation_id: str


@dataclass(frozen=True)
class WalletProviderHTTPRequestSpec:
    method: str
    path: str
    headers: dict
    json_body: object = None
    params: dict | None = None


@dataclass(frozen=True)
class WalletProviderTopUpInitiationResult:
    provider_reference: str
    provider_status: str
    raw_data: object


class BaseWalletProviderOutgoingAdapter(ABC):
    """Contrat de protocole sortant propre à chaque fournisseur."""

    @abstractmethod
    def build_topup_request(
        self,
        command,
    ):
        raise NotImplementedError

    @abstractmethod
    def parse_topup_response(
        self,
        *,
        response,
        command,
    ):
        raise NotImplementedError


def _normalize_text(
    value,
    *,
    field,
    max_length,
):
    if not isinstance(value, str):
        raise WalletProviderOutboundValidationError(
            f"Invalid {field}."
        )

    value = value.strip()

    if (
        not value
        or len(value) > max_length
        or "\r" in value
        or "\n" in value
    ):
        raise WalletProviderOutboundValidationError(
            f"Invalid {field}."
        )

    return value


def _normalize_topup_id(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise WalletProviderOutboundValidationError(
            "Invalid top-up id."
        )

    return value


def _normalize_currency(value):
    value = _normalize_text(
        value,
        field="currency",
        max_length=3,
    ).upper()

    if (
        len(value) != 3
        or not value.isalpha()
        or not value.isascii()
    ):
        raise WalletProviderOutboundValidationError(
            "Invalid currency."
        )

    return value


def _validate_result(result):
    if not isinstance(
        result,
        WalletProviderTopUpInitiationResult,
    ):
        raise WalletProviderOutboundProtocolError(
            "Provider adapter returned invalid result."
        )

    provider_reference = _normalize_text(
        result.provider_reference,
        field="provider reference",
        max_length=120,
    )

    provider_status = _normalize_text(
        result.provider_status,
        field="provider status",
        max_length=50,
    )

    return WalletProviderTopUpInitiationResult(
        provider_reference=provider_reference,
        provider_status=provider_status,
        raw_data=result.raw_data,
    )


class WalletProviderOutboundService:
    """Orchestration générique d'une initiation de recharge.

    Ce service ne connaît aucun endpoint ni format Airtel/Moov.
    """

    def __init__(
        self,
        *,
        api_client,
        adapter,
    ):
        if not isinstance(
            api_client,
            WalletProviderAPIClient,
        ):
            raise WalletProviderOutboundValidationError(
                "Invalid provider API client."
            )

        if not isinstance(
            adapter,
            BaseWalletProviderOutgoingAdapter,
        ):
            raise WalletProviderOutboundValidationError(
                "Invalid outgoing provider adapter."
            )

        self.api_client = api_client
        self.adapter = adapter

    def initiate_topup(
        self,
        *,
        topup_id,
        amount,
        currency,
        phone,
        idempotency_key,
        correlation_id=None,
    ):
        topup_id = _normalize_topup_id(
            topup_id
        )

        try:
            amount = _normalize_amount(
                amount
            )
        except InvalidWalletAmountError as exc:
            raise WalletProviderOutboundValidationError(
                "Invalid top-up amount."
            ) from exc

        currency = _normalize_currency(
            currency
        )

        phone = _normalize_text(
            phone,
            field="phone",
            max_length=20,
        )

        idempotency_key = _normalize_text(
            idempotency_key,
            field="idempotency key",
            max_length=120,
        )

        if correlation_id is None:
            correlation_id = str(
                uuid4()
            )
        else:
            correlation_id = _normalize_text(
                correlation_id,
                field="correlation id",
                max_length=120,
            )

        command = WalletProviderTopUpCommand(
            topup_id=topup_id,
            amount=amount,
            currency=currency,
            phone=phone,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

        try:
            request_spec = (
                self.adapter
                .build_topup_request(
                    command
                )
            )
        except WalletProviderOutboundError:
            raise
        except Exception as exc:
            raise WalletProviderOutboundProtocolError(
                "Provider adapter failed to build request."
            ) from exc

        if not isinstance(
            request_spec,
            WalletProviderHTTPRequestSpec,
        ):
            raise WalletProviderOutboundProtocolError(
                "Provider adapter returned invalid request specification."
            )

        try:
            response = self.api_client.request_json(
                method=request_spec.method,
                path=request_spec.path,
                headers=request_spec.headers,
                json_body=request_spec.json_body,
                params=request_spec.params,
            )

        except WalletProviderAuthError as exc:
            raise WalletProviderOutboundAuthenticationError(
                "Provider authentication failed."
            ) from exc

        except WalletProviderHTTPTimeoutError as exc:
            raise WalletProviderOutboundTimeoutError(
                "Provider request timed out."
            ) from exc

        except WalletProviderHTTPNetworkError as exc:
            raise WalletProviderOutboundUnavailableError(
                "Provider network is unavailable."
            ) from exc

        except WalletProviderHTTPStatusError as exc:
            raise WalletProviderOutboundRejectedError(
                status_code=exc.status_code,
            ) from exc

        except WalletProviderHTTPPayloadError as exc:
            raise WalletProviderOutboundProtocolError(
                "Provider returned invalid response payload."
            ) from exc

        try:
            result = (
                self.adapter
                .parse_topup_response(
                    response=response,
                    command=command,
                )
            )
        except WalletProviderOutboundError:
            raise
        except Exception as exc:
            raise WalletProviderOutboundProtocolError(
                "Provider adapter failed to parse response."
            ) from exc

        return _validate_result(
            result
        )
