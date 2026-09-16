from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core.models import (
    CustomUser,
    Driver,
    DriverWallet,
    WalletTopUp,
    WalletTransaction,
)
from core.services.wallet_provider_callback_service import (
    process_wallet_provider_callback,
)
from core.services.wallet_topup_service import (
    WalletTopUpConflictError,
)


class WalletProviderTerminalConsistencyTests(
    TestCase
):

    def setUp(self):
        user = CustomUser.objects.create_user(
            email="terminal-callback@example.com",
            phone="+23567779401",
            user_type="driver",
        )

        driver = Driver.objects.create(
            user=user,
            is_enabled=True,
        )

        self.wallet = DriverWallet.objects.create(
            driver=driver,
            balance=Decimal("1000.00"),
        )

    def create_topup(self, suffix):
        return WalletTopUp.objects.create(
            wallet=self.wallet,
            amount=Decimal("5000.00"),
            currency="XAF",
            provider=(
                WalletTopUp.Provider.AIRTEL_MONEY
            ),
            phone="+23566000200",
            provider_reference=f"remote-{suffix}",
            provider_status="pending",
            initiated_at=timezone.now(),
            idempotency_key=f"terminal-{suffix}",
            status=WalletTopUp.Status.PENDING,
        )

    def test_success_retry_rejects_conflicting_raw_status(self):
        topup = self.create_topup(
            "success-conflict"
        )

        process_wallet_provider_callback(
            topup_id=topup.pk,
            provider=topup.provider,
            provider_reference=
                topup.provider_reference,
            amount=topup.amount,
            callback_status=
                WalletTopUp.Status.SUCCESS,
            provider_status="COMPLETED",
        )

        with self.assertRaises(
            WalletTopUpConflictError
        ):
            process_wallet_provider_callback(
                topup_id=topup.pk,
                provider=topup.provider,
                provider_reference=
                    topup.provider_reference,
                amount=topup.amount,
                callback_status=
                    WalletTopUp.Status.SUCCESS,
                provider_status="REVERSED",
            )

        topup.refresh_from_db()
        self.wallet.refresh_from_db()

        self.assertEqual(
            topup.provider_status,
            "COMPLETED",
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("6000.00"),
        )

        self.assertEqual(
            WalletTransaction.objects.filter(
                wallet=self.wallet,
                type=WalletTransaction.Type.TOPUP,
            ).count(),
            1,
        )

    def test_success_retry_without_raw_status_preserves_existing(self):
        topup = self.create_topup(
            "success-missing"
        )

        process_wallet_provider_callback(
            topup_id=topup.pk,
            provider=topup.provider,
            provider_reference=
                topup.provider_reference,
            amount=topup.amount,
            callback_status=
                WalletTopUp.Status.SUCCESS,
            provider_status="COMPLETED",
        )

        result = process_wallet_provider_callback(
            topup_id=topup.pk,
            provider=topup.provider,
            provider_reference=
                topup.provider_reference,
            amount=topup.amount,
            callback_status=
                WalletTopUp.Status.SUCCESS,
        )

        topup.refresh_from_db()
        self.wallet.refresh_from_db()

        self.assertFalse(
            result.processed
        )

        self.assertEqual(
            topup.provider_status,
            "COMPLETED",
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("6000.00"),
        )

        self.assertEqual(
            WalletTransaction.objects.filter(
                wallet=self.wallet,
                type=WalletTransaction.Type.TOPUP,
            ).count(),
            1,
        )

    def test_failed_retry_rejects_conflicting_raw_status(self):
        topup = self.create_topup(
            "failed-conflict"
        )

        process_wallet_provider_callback(
            topup_id=topup.pk,
            provider=topup.provider,
            provider_reference=
                topup.provider_reference,
            amount=topup.amount,
            callback_status=
                WalletTopUp.Status.FAILED,
            provider_status="DECLINED",
            failure_reason="Insufficient funds.",
        )

        with self.assertRaises(
            WalletTopUpConflictError
        ):
            process_wallet_provider_callback(
                topup_id=topup.pk,
                provider=topup.provider,
                provider_reference=
                    topup.provider_reference,
                amount=topup.amount,
                callback_status=
                    WalletTopUp.Status.FAILED,
                provider_status="BLOCKED",
                failure_reason="Insufficient funds.",
            )

        topup.refresh_from_db()
        self.wallet.refresh_from_db()

        self.assertEqual(
            topup.provider_status,
            "DECLINED",
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

        self.assertFalse(
            WalletTransaction.objects.filter(
                wallet=self.wallet,
                type=WalletTransaction.Type.TOPUP,
            ).exists()
        )

    def test_failed_retry_without_raw_status_preserves_existing(self):
        topup = self.create_topup(
            "failed-missing"
        )

        process_wallet_provider_callback(
            topup_id=topup.pk,
            provider=topup.provider,
            provider_reference=
                topup.provider_reference,
            amount=topup.amount,
            callback_status=
                WalletTopUp.Status.FAILED,
            provider_status="DECLINED",
            failure_reason="Rejected.",
        )

        result = process_wallet_provider_callback(
            topup_id=topup.pk,
            provider=topup.provider,
            provider_reference=
                topup.provider_reference,
            amount=topup.amount,
            callback_status=
                WalletTopUp.Status.FAILED,
            failure_reason="Rejected.",
        )

        topup.refresh_from_db()

        self.assertFalse(
            result.processed
        )

        self.assertEqual(
            topup.provider_status,
            "DECLINED",
        )

    def test_legacy_success_without_provider_status_is_backfilled(self):
        topup = self.create_topup(
            "legacy-success"
        )

        process_wallet_provider_callback(
            topup_id=topup.pk,
            provider=topup.provider,
            provider_reference=
                topup.provider_reference,
            amount=topup.amount,
            callback_status=
                WalletTopUp.Status.SUCCESS,
            provider_status="COMPLETED",
        )

        WalletTopUp.objects.filter(
            pk=topup.pk
        ).update(
            provider_status=""
        )

        result = process_wallet_provider_callback(
            topup_id=topup.pk,
            provider=topup.provider,
            provider_reference=
                topup.provider_reference,
            amount=topup.amount,
            callback_status=
                WalletTopUp.Status.SUCCESS,
        )

        topup.refresh_from_db()
        self.wallet.refresh_from_db()

        self.assertFalse(
            result.processed
        )

        self.assertEqual(
            topup.provider_status,
            WalletTopUp.Status.SUCCESS,
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("6000.00"),
        )

        self.assertEqual(
            WalletTransaction.objects.filter(
                wallet=self.wallet,
                type=WalletTransaction.Type.TOPUP,
            ).count(),
            1,
        )

    def test_legacy_failed_without_provider_status_is_backfilled(self):
        topup = self.create_topup(
            "legacy-failed"
        )

        process_wallet_provider_callback(
            topup_id=topup.pk,
            provider=topup.provider,
            provider_reference=
                topup.provider_reference,
            amount=topup.amount,
            callback_status=
                WalletTopUp.Status.FAILED,
            provider_status="DECLINED",
            failure_reason="Rejected.",
        )

        WalletTopUp.objects.filter(
            pk=topup.pk
        ).update(
            provider_status=""
        )

        result = process_wallet_provider_callback(
            topup_id=topup.pk,
            provider=topup.provider,
            provider_reference=
                topup.provider_reference,
            amount=topup.amount,
            callback_status=
                WalletTopUp.Status.FAILED,
            failure_reason="Rejected.",
        )

        topup.refresh_from_db()
        self.wallet.refresh_from_db()

        self.assertFalse(
            result.processed
        )

        self.assertEqual(
            topup.provider_status,
            WalletTopUp.Status.FAILED,
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )
