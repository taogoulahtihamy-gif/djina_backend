from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import TestCase

from core.models import (
    CustomUser,
    Driver,
    DriverWallet,
    WalletTopUp,
    WalletTransaction,
)
from core.services.wallet_provider_outbound import (
    WalletProviderOutboundTimeoutError,
    WalletProviderTopUpInitiationResult,
)
from core.services.wallet_topup_initiation_service import (
    WalletTopUpInitiationConflictError,
    WalletTopUpInitiationConsistencyError,
    WalletTopUpInitiationError,
    WalletTopUpInitiationProviderError,
    WalletTopUpInitiationStateError,
    initiate_wallet_topup,
)


class WalletTopUpInitiationTests(TestCase):

    def setUp(self):
        user = CustomUser.objects.create_user(
            email="init-topup@example.com",
            phone="+23567779001",
            user_type="driver",
        )

        self.driver = Driver.objects.create(
            user=user,
            is_enabled=True,
        )

        self.wallet = DriverWallet.objects.create(
            driver=self.driver,
            balance=Decimal("1500.00"),
        )

        self.topup = WalletTopUp.objects.create(
            wallet=self.wallet,
            amount=Decimal("10000.00"),
            currency="XAF",
            provider=(
                WalletTopUp.Provider.AIRTEL_MONEY
            ),
            phone="+23566000000",
            idempotency_key=
                "topup-init-request-001",
            status=WalletTopUp.Status.PENDING,
        )

        self.outbound = Mock()

        self.outbound.initiate_topup.return_value = (
            WalletProviderTopUpInitiationResult(
                provider_reference=
                    "provider-ref-001",
                provider_status="pending",
                raw_data={
                    "provider_reference":
                        "provider-ref-001",
                    "status": "pending",
                },
            )
        )

        self.runtime = SimpleNamespace(
            outbound_service=self.outbound
        )

        self.runtime_builder = Mock(
            return_value=self.runtime
        )

    def initiate(self):
        return initiate_wallet_topup(
            topup_id=self.topup.pk,
            runtime_builder=
                self.runtime_builder,
        )

    def test_pending_topup_is_initiated(self):
        result = self.initiate()

        self.topup.refresh_from_db()

        self.assertTrue(
            result.initiated
        )

        self.assertEqual(
            self.topup.provider_reference,
            "provider-ref-001",
        )

        self.assertEqual(
            self.topup.status,
            WalletTopUp.Status.PENDING,
        )

    def test_runtime_uses_topup_provider(self):
        self.initiate()

        self.runtime_builder.assert_called_once_with(
            WalletTopUp.Provider.AIRTEL_MONEY
        )

    def test_server_owned_idempotency_key_is_used(self):
        self.initiate()

        kwargs = (
            self.outbound
            .initiate_topup
            .call_args
            .kwargs
        )

        self.assertEqual(
            kwargs["idempotency_key"],
            f"topup:{self.topup.pk}",
        )

        self.assertEqual(
            kwargs["correlation_id"],
            f"djina-topup:{self.topup.pk}",
        )

    def test_initiation_never_changes_wallet_balance(self):
        self.initiate()

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1500.00"),
        )

        self.assertFalse(
            WalletTransaction.objects.exists()
        )

    def test_existing_provider_reference_skips_network(self):
        self.topup.provider_reference = (
            "already-remote"
        )

        self.topup.save(
            update_fields=[
                "provider_reference",
                "updated_at",
            ]
        )

        result = self.initiate()

        self.assertFalse(
            result.initiated
        )

        self.assertEqual(
            result.provider_reference,
            "already-remote",
        )

        self.runtime_builder.assert_not_called()
        self.outbound.initiate_topup.assert_not_called()

    def test_terminal_topup_rejected_before_network(self):
        WalletTopUp.objects.filter(
            pk=self.topup.pk
        ).update(
            status=WalletTopUp.Status.FAILED
        )

        with self.assertRaises(
            WalletTopUpInitiationStateError
        ):
            self.initiate()

        self.runtime_builder.assert_not_called()

    def test_other_provider_is_not_remotely_initiated(self):
        self.topup.provider = (
            WalletTopUp.Provider.OTHER
        )

        self.topup.save(
            update_fields=[
                "provider",
                "updated_at",
            ]
        )

        with self.assertRaises(
            WalletTopUpInitiationProviderError
        ):
            self.initiate()

        self.runtime_builder.assert_not_called()

    def test_provider_failure_does_not_modify_topup(self):
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
        self.wallet.refresh_from_db()

        self.assertIsNone(
            self.topup.provider_reference
        )

        self.assertEqual(
            self.topup.status,
            WalletTopUp.Status.PENDING,
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("1500.00"),
        )

    def test_duplicate_provider_reference_is_rejected(self):
        WalletTopUp.objects.create(
            wallet=self.wallet,
            amount=Decimal("5000.00"),
            currency="XAF",
            provider=(
                WalletTopUp.Provider.AIRTEL_MONEY
            ),
            phone="+23566000001",
            provider_reference=
                "provider-ref-001",
            idempotency_key=
                "other-topup-init",
            status=WalletTopUp.Status.PENDING,
        )

        with self.assertRaises(
            WalletTopUpInitiationConflictError
        ):
            self.initiate()

        self.topup.refresh_from_db()

        self.assertIsNone(
            self.topup.provider_reference
        )

    def test_same_reference_saved_during_network_is_safe(self):
        def remote_call(**kwargs):
            WalletTopUp.objects.filter(
                pk=self.topup.pk
            ).update(
                provider_reference=
                    "provider-ref-001"
            )

            return (
                WalletProviderTopUpInitiationResult(
                    provider_reference=
                        "provider-ref-001",
                    provider_status="pending",
                    raw_data={},
                )
            )

        self.outbound.initiate_topup.side_effect = (
            remote_call
        )

        result = self.initiate()

        self.assertFalse(
            result.initiated
        )

        self.topup.refresh_from_db()

        self.assertEqual(
            self.topup.provider_reference,
            "provider-ref-001",
        )

    def test_different_reference_saved_during_network_is_detected(self):
        def remote_call(**kwargs):
            WalletTopUp.objects.filter(
                pk=self.topup.pk
            ).update(
                provider_reference=
                    "different-ref"
            )

            return (
                WalletProviderTopUpInitiationResult(
                    provider_reference=
                        "provider-ref-001",
                    provider_status="pending",
                    raw_data={},
                )
            )

        self.outbound.initiate_topup.side_effect = (
            remote_call
        )

        with self.assertRaises(
            WalletTopUpInitiationConsistencyError
        ):
            self.initiate()

    def test_missing_topup_rejected(self):
        with self.assertRaises(
            WalletTopUpInitiationError
        ):
            initiate_wallet_topup(
                topup_id=999999,
                runtime_builder=
                    self.runtime_builder,
            )
