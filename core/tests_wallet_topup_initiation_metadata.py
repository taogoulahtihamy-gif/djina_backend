from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import TestCase
from rest_framework.test import APITestCase

from core.models import (
    CustomUser,
    Driver,
    DriverWallet,
    WalletTopUp,
)
from core.services.wallet_provider_outbound import (
    WalletProviderOutboundTimeoutError,
    WalletProviderTopUpInitiationResult,
)
from core.services.wallet_topup_initiation_service import (
    WalletTopUpInitiationConsistencyError,
    WalletTopUpInitiationProviderError,
    initiate_wallet_topup,
)


class WalletTopUpInitiationMetadataTests(
    TestCase
):
    def setUp(self):
        user = CustomUser.objects.create_user(
            email="metadata-driver@example.com",
            phone="+23567779201",
            user_type="driver",
        )

        driver = Driver.objects.create(
            user=user,
            is_enabled=True,
        )

        self.wallet = DriverWallet.objects.create(
            driver=driver,
            balance=Decimal("3500.00"),
        )

        self.topup = WalletTopUp.objects.create(
            wallet=self.wallet,
            amount=Decimal("12000.00"),
            currency="XAF",
            provider=(
                WalletTopUp.Provider.AIRTEL_MONEY
            ),
            phone="+23566000010",
            idempotency_key=
                "metadata-topup-001",
            status=WalletTopUp.Status.PENDING,
        )

        self.outbound = Mock()

        self.outbound.initiate_topup.return_value = (
            WalletProviderTopUpInitiationResult(
                provider_reference=
                    "metadata-ref-001",
                provider_status="pending",
                raw_data={},
            )
        )

        self.runtime_builder = Mock(
            return_value=SimpleNamespace(
                outbound_service=self.outbound
            )
        )

    def initiate(self):
        return initiate_wallet_topup(
            topup_id=self.topup.pk,
            runtime_builder=
                self.runtime_builder,
        )

    def test_new_topup_has_no_initiation_metadata(self):
        self.assertEqual(
            self.topup.provider_status,
            "",
        )
        self.assertIsNone(
            self.topup.initiated_at
        )

    def test_success_persists_provider_metadata(self):
        result = self.initiate()

        self.topup.refresh_from_db()

        self.assertTrue(result.initiated)

        self.assertEqual(
            self.topup.provider_reference,
            "metadata-ref-001",
        )

        self.assertEqual(
            self.topup.provider_status,
            "pending",
        )

        self.assertIsNotNone(
            self.topup.initiated_at
        )

    def test_retry_returns_stored_provider_status(self):
        first = self.initiate()
        first_time = first.topup.initiated_at

        self.runtime_builder.reset_mock()
        self.outbound.reset_mock()

        second = self.initiate()

        self.assertFalse(
            second.initiated
        )

        self.assertEqual(
            second.provider_status,
            "pending",
        )

        self.assertEqual(
            second.topup.initiated_at,
            first_time,
        )

        self.runtime_builder.assert_not_called()
        self.outbound.initiate_topup.assert_not_called()

    def test_failure_does_not_create_metadata(self):
        self.outbound.initiate_topup.side_effect = (
            WalletProviderOutboundTimeoutError(
                "timeout"
            )
        )

        with self.assertRaises(
            WalletTopUpInitiationProviderError
        ):
            self.initiate()

        self.topup.refresh_from_db()

        self.assertIsNone(
            self.topup.provider_reference
        )

        self.assertEqual(
            self.topup.provider_status,
            "",
        )

        self.assertIsNone(
            self.topup.initiated_at
        )

    def test_invalid_provider_status_is_rejected(self):
        self.outbound.initiate_topup.return_value = (
            WalletProviderTopUpInitiationResult(
                provider_reference=
                    "metadata-ref-002",
                provider_status="x" * 51,
                raw_data={},
            )
        )

        with self.assertRaises(
            WalletTopUpInitiationConsistencyError
        ):
            self.initiate()

        self.topup.refresh_from_db()

        self.assertIsNone(
            self.topup.provider_reference
        )

        self.assertEqual(
            self.topup.provider_status,
            "",
        )

        self.assertIsNone(
            self.topup.initiated_at
        )

    def test_wallet_balance_remains_unchanged(self):
        self.initiate()

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("3500.00"),
        )

        self.assertEqual(
            self.wallet.reserved_balance,
            Decimal("0.00"),
        )
