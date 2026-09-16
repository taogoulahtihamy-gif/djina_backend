from dataclasses import dataclass, field
from urllib.parse import urlparse

from django.conf import settings

from core.services.wallet_provider_adapters import (
    MockWalletProviderAdapter,
)


class WalletProviderRegistryError(Exception):
    pass


class WalletProviderUnknownError(WalletProviderRegistryError):
    pass


class WalletProviderConfigurationError(WalletProviderRegistryError):
    pass


class WalletProviderDisabledError(WalletProviderRegistryError):
    pass


class WalletProviderNotImplementedError(WalletProviderRegistryError):
    pass


@dataclass(frozen=True)
class WalletProviderConfig:
    key: str
    enabled: bool
    environment: str
    base_url: str
    client_id: str
    client_secret: str = field(repr=False)
    webhook_secret: str = field(repr=False)
    timeout_seconds: float


_PROVIDER_SETTINGS = {
    "mock": {
        "enabled": "WALLET_MOCK_PROVIDER_ENABLED",
        "environment": "WALLET_MOCK_PROVIDER_ENVIRONMENT",
        "base_url": "WALLET_MOCK_PROVIDER_BASE_URL",
        "client_id": None,
        "client_secret": None,
        "webhook_secret": "WALLET_MOCK_PROVIDER_SECRET",
        "timeout": "WALLET_MOCK_PROVIDER_TIMEOUT_SECONDS",
    },
    "airtel_money": {
        "enabled": "WALLET_AIRTEL_ENABLED",
        "environment": "WALLET_AIRTEL_ENVIRONMENT",
        "base_url": "WALLET_AIRTEL_BASE_URL",
        "client_id": "WALLET_AIRTEL_CLIENT_ID",
        "client_secret": "WALLET_AIRTEL_CLIENT_SECRET",
        "webhook_secret": "WALLET_AIRTEL_WEBHOOK_SECRET",
        "timeout": "WALLET_AIRTEL_TIMEOUT_SECONDS",
    },
    "moov_money": {
        "enabled": "WALLET_MOOV_ENABLED",
        "environment": "WALLET_MOOV_ENVIRONMENT",
        "base_url": "WALLET_MOOV_BASE_URL",
        "client_id": "WALLET_MOOV_CLIENT_ID",
        "client_secret": "WALLET_MOOV_CLIENT_SECRET",
        "webhook_secret": "WALLET_MOOV_WEBHOOK_SECRET",
        "timeout": "WALLET_MOOV_TIMEOUT_SECONDS",
    },
}


def _read_setting(name, default=""):
    if name is None:
        return default

    return getattr(settings, name, default)


def _parse_bool(value):
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()

        if normalized in {"1", "true", "yes", "on"}:
            return True

        if normalized in {"0", "false", "no", "off", ""}:
            return False

    raise WalletProviderConfigurationError(
        "Invalid provider enabled flag."
    )


def _parse_timeout(value):
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise WalletProviderConfigurationError(
            "Invalid provider timeout."
        ) from exc

    if timeout <= 0 or timeout > 60:
        raise WalletProviderConfigurationError(
            "Provider timeout must be between 0 and 60 seconds."
        )

    return timeout


def _clean(value):
    if value is None:
        return ""

    return str(value).strip()


def get_wallet_provider_config(key):
    spec = _PROVIDER_SETTINGS.get(key)

    if spec is None:
        raise WalletProviderUnknownError(
            f"Unknown wallet provider: {key}"
        )

    config = WalletProviderConfig(
        key=key,
        enabled=_parse_bool(
            _read_setting(spec["enabled"], False)
        ),
        environment=_clean(
            _read_setting(
                spec["environment"],
                "mock" if key == "mock" else "sandbox",
            )
        ),
        base_url=_clean(
            _read_setting(spec["base_url"])
        ),
        client_id=_clean(
            _read_setting(spec["client_id"])
        ),
        client_secret=_clean(
            _read_setting(spec["client_secret"])
        ),
        webhook_secret=_clean(
            _read_setting(spec["webhook_secret"])
        ),
        timeout_seconds=_parse_timeout(
            _read_setting(spec["timeout"], "10")
        ),
    )

    return config


def validate_wallet_provider_config(config):
    allowed_environments = (
        {"mock"}
        if config.key == "mock"
        else {"sandbox", "production"}
    )

    if config.environment not in allowed_environments:
        raise WalletProviderConfigurationError(
            "Invalid provider environment."
        )

    if not config.enabled:
        return config

    if not config.webhook_secret:
        raise WalletProviderConfigurationError(
            "Enabled provider requires a webhook secret."
        )

    if config.key == "mock":
        return config

    missing = [
        name
        for name, value in (
            ("base_url", config.base_url),
            ("client_id", config.client_id),
            ("client_secret", config.client_secret),
        )
        if not value
    ]

    if missing:
        raise WalletProviderConfigurationError(
            "Enabled provider is missing: "
            + ", ".join(missing)
        )

    parsed = urlparse(config.base_url)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WalletProviderConfigurationError(
            "Provider base URL is invalid."
        )

    if (
        config.environment == "production"
        and parsed.scheme != "https"
    ):
        raise WalletProviderConfigurationError(
            "Production provider URL must use HTTPS."
        )

    return config


def require_wallet_provider_config(key):
    config = get_wallet_provider_config(key)

    if not config.enabled:
        raise WalletProviderDisabledError(
            f"Wallet provider {key} is disabled."
        )

    return validate_wallet_provider_config(config)


def build_wallet_provider_adapter(key):
    config = require_wallet_provider_config(key)

    if key == "mock":
        return MockWalletProviderAdapter(
            secret=config.webhook_secret
        )

    # Les protocoles Airtel/Moov ne sont volontairement
    # pas inventés avant intégration de leur documentation réelle.
    raise WalletProviderNotImplementedError(
        f"Adapter for {key} is not implemented yet."
    )
