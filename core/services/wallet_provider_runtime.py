from dataclasses import dataclass

from core.services.wallet_provider_adapters import (
    MockWalletProviderOutgoingAdapter,
)
from core.services.wallet_provider_auth import (
    NoAuthWalletProviderStrategy,
)
from core.services.wallet_provider_client import (
    WalletProviderAPIClient,
)
from core.services.wallet_provider_http import (
    WalletProviderHTTPConfigurationError,
    build_wallet_provider_http_client,
)
from core.services.wallet_provider_outbound import (
    WalletProviderOutboundService,
)
from core.services.wallet_provider_registry import (
    WalletProviderNotImplementedError,
    build_wallet_provider_adapter,
    require_wallet_provider_config,
)


class WalletProviderRuntimeError(Exception):
    pass


class WalletProviderRuntimeConfigurationError(
    WalletProviderRuntimeError
):
    pass


@dataclass(frozen=True)
class WalletProviderRuntime:
    key: str
    config: object
    inbound_adapter: object
    outbound_adapter: object
    http_client: object
    auth_strategy: object
    api_client: object
    outbound_service: object


def build_wallet_provider_runtime(
    key,
    *,
    session=None,
):
    """Construit toutes les briques d'un fournisseur implémenté.

    Actuellement seul `mock` possède volontairement un protocole
    entrant + sortant complet.

    Airtel Money et Moov Money restent explicitement non implémentés
    jusqu'à intégration de leur documentation officielle.
    """

    config = require_wallet_provider_config(
        key
    )

    if key in {
        "airtel_money",
        "moov_money",
    }:
        raise WalletProviderNotImplementedError(
            f"Provider protocol for {key} is not implemented yet."
        )

    if key != "mock":
        raise WalletProviderNotImplementedError(
            f"Provider runtime for {key} is not implemented."
        )

    if not config.base_url:
        raise WalletProviderRuntimeConfigurationError(
            "Mock provider runtime requires a base URL."
        )

    inbound_adapter = build_wallet_provider_adapter(
        "mock"
    )

    outbound_adapter = (
        MockWalletProviderOutgoingAdapter()
    )

    try:
        http_client = (
            build_wallet_provider_http_client(
                config,
                session=session,
            )
        )
    except WalletProviderHTTPConfigurationError as exc:
        raise WalletProviderRuntimeConfigurationError(
            "Invalid provider HTTP configuration."
        ) from exc

    auth_strategy = (
        NoAuthWalletProviderStrategy()
    )

    api_client = WalletProviderAPIClient(
        http_client=http_client,
        auth_strategy=auth_strategy,
    )

    outbound_service = (
        WalletProviderOutboundService(
            api_client=api_client,
            adapter=outbound_adapter,
        )
    )

    return WalletProviderRuntime(
        key=key,
        config=config,
        inbound_adapter=inbound_adapter,
        outbound_adapter=outbound_adapter,
        http_client=http_client,
        auth_strategy=auth_strategy,
        api_client=api_client,
        outbound_service=outbound_service,
    )
