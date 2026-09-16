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
from core.services.wallet_service import (
    WalletNotActiveError,
)
from core.services.wallet_topup_service import (
    WalletTopUpStateError,
)


class WalletProviderCallbackMetadataTests(
    TestCase
):

    def setUp(self):
        user = CustomUser.objects.create_user(
            email="callback-meta@example.com",
            phone="+23567779301",
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

    def create_topup(
        self,
        *,
        suffix,
        amount="5000.00",
    ):
        return WalletTopUp.objects.create(
            wallet=self.wallet,
            amount=Decimal(amount),
            currency="XAF",
            provider=(
                WalletTopUp.Provider.AIRTEL_MONEY
            ),
            phone="+23566000100",
            provider_reference=(
                f"remote-{suffix}"
            ),
            provider_status="pending",
            initiated_at=timezone.now(),
            idempotency_key=(
                f"callback-meta-{suffix}"
            ),
            status=WalletTopUp.Status.PENDING,
        )

    def test_success_callback_updates_provider_status(self):
        topup = self.create_topup(
            suffix="success"
        )

        result = (
            process_wallet_provider_callback(
                topup_id=topup.pk,
                provider=topup.provider,
                provider_reference=
                    topup.provider_reference,
                amount=topup.amount,
                callback_status=
                    WalletTopUp.Status.SUCCESS,
            )
        )

        topup.refresh_from_db()
        self.wallet.refresh_from_db()

        self.assertTrue(
            result.processed
        )

        self.assertEqual(
            topup.status,
            WalletTopUp.Status.SUCCESS,
        )

        self.assertEqual(
            topup.provider_status,
            WalletTopUp.Status.SUCCESS,
        )

        self.assertIsNotNone(
            topup.confirmed_at
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("6000.00"),
        )

        self.assertEqual(
            WalletTransaction.objects.filter(
                wallet=self.wallet,
                type=
                    WalletTransaction.Type.TOPUP,
            ).count(),
            1,
        )

    def test_success_retry_does_not_double_credit(self):
        topup = self.create_topup(
            suffix="success-retry"
        )

        kwargs = {
            "topup_id": topup.pk,
            "provider": topup.provider,
            "provider_reference":
                topup.provider_reference,
            "amount": topup.amount,
            "callback_status":
                WalletTopUp.Status.SUCCESS,
        }

        first = (
            process_wallet_provider_callback(
                **kwargs
            )
        )

        second = (
            process_wallet_provider_callback(
                **kwargs
            )
        )

        self.wallet.refresh_from_db()
        topup.refresh_from_db()

        self.assertTrue(
            first.processed
        )

        self.assertFalse(
            second.processed
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("6000.00"),
        )

        self.assertEqual(
            WalletTransaction.objects.filter(
                wallet=self.wallet,
                type=
                    WalletTransaction.Type.TOPUP,
            ).count(),
            1,
        )

        self.assertEqual(
            topup.provider_status,
            WalletTopUp.Status.SUCCESS,
        )

    def test_failed_callback_updates_provider_status_without_money(self):
        topup = self.create_topup(
            suffix="failed"
        )

        result = (
            process_wallet_provider_callback(
                topup_id=topup.pk,
                provider=topup.provider,
                provider_reference=
                    topup.provider_reference,
                amount=topup.amount,
                callback_status=
                    WalletTopUp.Status.FAILED,
                failure_reason=
                    "Provider rejected payment.",
            )
        )

        topup.refresh_from_db()
        self.wallet.refresh_from_db()

        self.assertTrue(
            result.processed
        )

        self.assertEqual(
            topup.status,
            WalletTopUp.Status.FAILED,
        )

        self.assertEqual(
            topup.provider_status,
            WalletTopUp.Status.FAILED,
        )

        self.assertIsNone(
            topup.confirmed_at
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

        self.assertFalse(
            WalletTransaction.objects.filter(
                wallet=self.wallet,
                type=
                    WalletTransaction.Type.TOPUP,
            ).exists()
        )

    def test_failed_retry_is_idempotent(self):
        topup = self.create_topup(
            suffix="failed-retry"
        )

        kwargs = {
            "topup_id": topup.pk,
            "provider": topup.provider,
            "provider_reference":
                topup.provider_reference,
            "amount": topup.amount,
            "callback_status":
                WalletTopUp.Status.FAILED,
            "failure_reason":
                "Insufficient funds.",
        }

        first = (
            process_wallet_provider_callback(
                **kwargs
            )
        )

        second = (
            process_wallet_provider_callback(
                **kwargs
            )
        )

        self.wallet.refresh_from_db()

        self.assertTrue(
            first.processed
        )

        self.assertFalse(
            second.processed
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

        self.assertFalse(
            WalletTransaction.objects.filter(
                wallet=self.wallet,
                type=
                    WalletTransaction.Type.TOPUP,
            ).exists()
        )

    def test_explicit_raw_provider_status_is_preserved(self):
        topup = self.create_topup(
            suffix="raw-status"
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

        topup.refresh_from_db()

        self.assertEqual(
            topup.status,
            WalletTopUp.Status.SUCCESS,
        )

        self.assertEqual(
            topup.provider_status,
            "COMPLETED",
        )

    def test_wallet_failure_rolls_back_provider_status(self):
        topup = self.create_topup(
            suffix="blocked"
        )

        self.wallet.status = (
            DriverWallet.Status.BLOCKED
        )

        self.wallet.save(
            update_fields=[
                "status",
                "updated_at",
            ]
        )

        with self.assertRaises(
            WalletNotActiveError
        ):
            process_wallet_provider_callback(
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

        self.assertEqual(
            topup.status,
            WalletTopUp.Status.PENDING,
        )

        self.assertEqual(
            topup.provider_status,
            "pending",
        )

        self.assertIsNone(
            topup.confirmed_at
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

        self.assertFalse(
            WalletTransaction.objects.filter(
                wallet=self.wallet,
                type=
                    WalletTransaction.Type.TOPUP,
            ).exists()
        )

    def test_failed_topup_cannot_later_be_confirmed(self):
        topup = self.create_topup(
            suffix="cross-state"
        )

        process_wallet_provider_callback(
            topup_id=topup.pk,
            provider=topup.provider,
            provider_reference=
                topup.provider_reference,
            amount=topup.amount,
            callback_status=
                WalletTopUp.Status.FAILED,
            failure_reason="Rejected.",
        )

        with self.assertRaises(
            WalletTopUpStateError
        ):
            process_wallet_provider_callback(
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

        self.assertEqual(
            topup.status,
            WalletTopUp.Status.FAILED,
        )

        self.assertEqual(
            topup.provider_status,
            WalletTopUp.Status.FAILED,
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )
