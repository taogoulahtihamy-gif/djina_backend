from django.test import SimpleTestCase, override_settings

from core.services.wallet_provider_adapters import (
    MockWalletProviderAdapter,
)
from core.services.wallet_provider_registry import (
    WalletProviderConfigurationError,
    WalletProviderDisabledError,
    WalletProviderNotImplementedError,
    WalletProviderUnknownError,
    build_wallet_provider_adapter,
    get_wallet_provider_config,
    require_wallet_provider_config,
    validate_wallet_provider_config,
)


class WalletProviderRegistryTests(SimpleTestCase):

    def test_unknown_provider_rejected(self):
        with self.assertRaises(
            WalletProviderUnknownError
        ):
            get_wallet_provider_config(
                "unknown-provider"
            )

    @override_settings(
        WALLET_AIRTEL_ENABLED=False,
    )
    def test_disabled_airtel_config_can_be_read(self):
        config = get_wallet_provider_config(
            "airtel_money"
        )

        self.assertFalse(config.enabled)

    @override_settings(
        WALLET_MOCK_PROVIDER_ENABLED=True,
        WALLET_MOCK_PROVIDER_SECRET="test-secret",
        WALLET_MOCK_PROVIDER_ENVIRONMENT="mock",
        WALLET_MOCK_PROVIDER_TIMEOUT_SECONDS="5",
    )
    def test_mock_config_is_valid(self):
        config = require_wallet_provider_config(
            "mock"
        )

        self.assertTrue(config.enabled)
        self.assertEqual(
            config.webhook_secret,
            "test-secret",
        )
        self.assertEqual(
            config.timeout_seconds,
            5.0,
        )

    @override_settings(
        WALLET_MOCK_PROVIDER_ENABLED=False,
    )
    def test_disabled_mock_is_rejected_when_required(self):
        with self.assertRaises(
            WalletProviderDisabledError
        ):
            require_wallet_provider_config(
                "mock"
            )

    @override_settings(
        WALLET_MOCK_PROVIDER_ENABLED=True,
        WALLET_MOCK_PROVIDER_SECRET="",
    )
    def test_enabled_mock_requires_secret(self):
        with self.assertRaises(
            WalletProviderConfigurationError
        ):
            require_wallet_provider_config(
                "mock"
            )

    @override_settings(
        WALLET_MOCK_PROVIDER_ENABLED="perhaps",
    )
    def test_invalid_boolean_rejected(self):
        with self.assertRaises(
            WalletProviderConfigurationError
        ):
            get_wallet_provider_config(
                "mock"
            )

    @override_settings(
        WALLET_MOCK_PROVIDER_TIMEOUT_SECONDS="0",
    )
    def test_invalid_timeout_rejected(self):
        with self.assertRaises(
            WalletProviderConfigurationError
        ):
            get_wallet_provider_config(
                "mock"
            )

    @override_settings(
        WALLET_AIRTEL_ENABLED=True,
        WALLET_AIRTEL_ENVIRONMENT="sandbox",
        WALLET_AIRTEL_BASE_URL="",
        WALLET_AIRTEL_CLIENT_ID="",
        WALLET_AIRTEL_CLIENT_SECRET="",
        WALLET_AIRTEL_WEBHOOK_SECRET="secret",
    )
    def test_enabled_airtel_requires_credentials(self):
        with self.assertRaises(
            WalletProviderConfigurationError
        ):
            require_wallet_provider_config(
                "airtel_money"
            )

    @override_settings(
        WALLET_AIRTEL_ENABLED=True,
        WALLET_AIRTEL_ENVIRONMENT="production",
        WALLET_AIRTEL_BASE_URL="https://payments.example.test",
        WALLET_AIRTEL_CLIENT_ID="client",
        WALLET_AIRTEL_CLIENT_SECRET="secret",
        WALLET_AIRTEL_WEBHOOK_SECRET="webhook",
        WALLET_AIRTEL_TIMEOUT_SECONDS="12",
    )
    def test_valid_production_airtel_config(self):
        config = require_wallet_provider_config(
            "airtel_money"
        )

        self.assertTrue(config.enabled)
        self.assertEqual(
            config.environment,
            "production",
        )
        self.assertEqual(
            config.timeout_seconds,
            12.0,
        )

    @override_settings(
        WALLET_AIRTEL_ENABLED=True,
        WALLET_AIRTEL_ENVIRONMENT="production",
        WALLET_AIRTEL_BASE_URL="http://payments.example.test",
        WALLET_AIRTEL_CLIENT_ID="client",
        WALLET_AIRTEL_CLIENT_SECRET="secret",
        WALLET_AIRTEL_WEBHOOK_SECRET="webhook",
    )
    def test_production_requires_https(self):
        config = get_wallet_provider_config(
            "airtel_money"
        )

        with self.assertRaises(
            WalletProviderConfigurationError
        ):
            validate_wallet_provider_config(
                config
            )

    @override_settings(
        WALLET_MOCK_PROVIDER_ENABLED=True,
        WALLET_MOCK_PROVIDER_SECRET="test-secret",
    )
    def test_mock_adapter_can_be_built(self):
        adapter = build_wallet_provider_adapter(
            "mock"
        )

        self.assertIsInstance(
            adapter,
            MockWalletProviderAdapter,
        )

    @override_settings(
        WALLET_MOOV_ENABLED=True,
        WALLET_MOOV_ENVIRONMENT="sandbox",
        WALLET_MOOV_BASE_URL="https://sandbox.example.test",
        WALLET_MOOV_CLIENT_ID="client",
        WALLET_MOOV_CLIENT_SECRET="secret",
        WALLET_MOOV_WEBHOOK_SECRET="webhook",
    )
    def test_real_adapter_is_not_faked(self):
        with self.assertRaises(
            WalletProviderNotImplementedError
        ):
            build_wallet_provider_adapter(
                "moov_money"
            )
