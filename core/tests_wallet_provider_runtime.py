from unittest.mock import Mock

from django.test import (
    SimpleTestCase,
    override_settings,
)

from core.services.wallet_provider_adapters import (
    MockWalletProviderAdapter,
    MockWalletProviderOutgoingAdapter,
)
from core.services.wallet_provider_auth import (
    NoAuthWalletProviderStrategy,
)
from core.services.wallet_provider_outbound import (
    WalletProviderOutboundService,
)
from core.services.wallet_provider_registry import (
    WalletProviderDisabledError,
    WalletProviderNotImplementedError,
    get_wallet_provider_config,
)
from core.services.wallet_provider_runtime import (
    WalletProviderRuntimeConfigurationError,
    build_wallet_provider_runtime,
)


MOCK_SETTINGS = {
    "WALLET_MOCK_PROVIDER_ENABLED": True,
    "WALLET_MOCK_PROVIDER_SECRET":
        "mock-webhook-super-secret",
    "WALLET_MOCK_PROVIDER_ENVIRONMENT":
        "mock",
    "WALLET_MOCK_PROVIDER_BASE_URL":
        "https://mock-payments.example.test/api",
    "WALLET_MOCK_PROVIDER_TIMEOUT_SECONDS":
        "7",
}


@override_settings(**MOCK_SETTINGS)
class WalletProviderRuntimeTests(
    SimpleTestCase
):

    def setUp(self):
        self.session = Mock()

    def test_mock_runtime_builds_full_stack(self):
        runtime = build_wallet_provider_runtime(
            "mock",
            session=self.session,
        )

        self.assertEqual(
            runtime.key,
            "mock",
        )

        self.assertIsInstance(
            runtime.inbound_adapter,
            MockWalletProviderAdapter,
        )

        self.assertIsInstance(
            runtime.outbound_adapter,
            MockWalletProviderOutgoingAdapter,
        )

        self.assertIsInstance(
            runtime.auth_strategy,
            NoAuthWalletProviderStrategy,
        )

        self.assertIsInstance(
            runtime.outbound_service,
            WalletProviderOutboundService,
        )

        self.assertEqual(
            runtime.http_client.timeout_seconds,
            7.0,
        )

    def test_injected_http_session_is_preserved(self):
        runtime = build_wallet_provider_runtime(
            "mock",
            session=self.session,
        )

        self.assertIs(
            runtime.http_client.session,
            self.session,
        )

    def test_mock_outbound_flow_uses_composed_stack(self):
        response = Mock()
        response.status_code = 202
        response.headers = {
            "Content-Type":
                "application/json",
        }
        response.json.return_value = {
            "provider_reference":
                "mock-ref-001",
            "status": "pending",
        }

        self.session.request.return_value = (
            response
        )

        runtime = build_wallet_provider_runtime(
            "mock",
            session=self.session,
        )

        result = (
            runtime.outbound_service
            .initiate_topup(
                topup_id=42,
                amount="10000.00",
                currency="XAF",
                phone="+23566000000",
                idempotency_key=
                    "djina-mock-topup-42",
                correlation_id=
                    "corr-mock-42",
            )
        )

        self.assertEqual(
            result.provider_reference,
            "mock-ref-001",
        )

        self.assertEqual(
            result.provider_status,
            "pending",
        )

        call = self.session.request.call_args

        self.assertEqual(
            call.kwargs["method"],
            "POST",
        )

        self.assertEqual(
            call.kwargs["url"],
            (
                "https://mock-payments."
                "example.test/api/topups"
            ),
        )

        self.assertEqual(
            call.kwargs["headers"][
                "X-DJINA-Idempotency-Key"
            ],
            "djina-mock-topup-42",
        )

        self.assertEqual(
            call.kwargs["headers"][
                "X-DJINA-Correlation-ID"
            ],
            "corr-mock-42",
        )

    @override_settings(
        WALLET_MOCK_PROVIDER_ENABLED=False
    )
    def test_disabled_provider_cannot_build_runtime(self):
        with self.assertRaises(
            WalletProviderDisabledError
        ):
            build_wallet_provider_runtime(
                "mock",
                session=self.session,
            )

    @override_settings(
        WALLET_MOCK_PROVIDER_BASE_URL=""
    )
    def test_mock_runtime_requires_base_url(self):
        with self.assertRaises(
            WalletProviderRuntimeConfigurationError
        ):
            build_wallet_provider_runtime(
                "mock",
                session=self.session,
            )

    @override_settings(
        WALLET_AIRTEL_ENABLED=True,
        WALLET_AIRTEL_ENVIRONMENT="sandbox",
        WALLET_AIRTEL_BASE_URL=
            "https://sandbox.airtel.example.test",
        WALLET_AIRTEL_CLIENT_ID="client",
        WALLET_AIRTEL_CLIENT_SECRET=
            "airtel-client-secret",
        WALLET_AIRTEL_WEBHOOK_SECRET=
            "airtel-webhook-secret",
        WALLET_AIRTEL_TIMEOUT_SECONDS="10",
    )
    def test_airtel_protocol_is_not_faked(self):
        with self.assertRaises(
            WalletProviderNotImplementedError
        ):
            build_wallet_provider_runtime(
                "airtel_money",
                session=self.session,
            )

        self.session.request.assert_not_called()

    @override_settings(
        WALLET_MOOV_ENABLED=True,
        WALLET_MOOV_ENVIRONMENT="sandbox",
        WALLET_MOOV_BASE_URL=
            "https://sandbox.moov.example.test",
        WALLET_MOOV_CLIENT_ID="client",
        WALLET_MOOV_CLIENT_SECRET=
            "moov-client-secret",
        WALLET_MOOV_WEBHOOK_SECRET=
            "moov-webhook-secret",
        WALLET_MOOV_TIMEOUT_SECONDS="10",
    )
    def test_moov_protocol_is_not_faked(self):
        with self.assertRaises(
            WalletProviderNotImplementedError
        ):
            build_wallet_provider_runtime(
                "moov_money",
                session=self.session,
            )

        self.session.request.assert_not_called()

    @override_settings(
        WALLET_AIRTEL_ENABLED=True,
        WALLET_AIRTEL_ENVIRONMENT="sandbox",
        WALLET_AIRTEL_BASE_URL=
            "https://sandbox.airtel.example.test",
        WALLET_AIRTEL_CLIENT_ID="visible-client-id",
        WALLET_AIRTEL_CLIENT_SECRET=
            "DO-NOT-LEAK-CLIENT-SECRET",
        WALLET_AIRTEL_WEBHOOK_SECRET=
            "DO-NOT-LEAK-WEBHOOK-SECRET",
    )
    def test_provider_config_repr_hides_secrets(self):
        config = get_wallet_provider_config(
            "airtel_money"
        )

        representation = repr(config)

        self.assertNotIn(
            "DO-NOT-LEAK-CLIENT-SECRET",
            representation,
        )

        self.assertNotIn(
            "DO-NOT-LEAK-WEBHOOK-SECRET",
            representation,
        )

        self.assertIn(
            "visible-client-id",
            representation,
        )
