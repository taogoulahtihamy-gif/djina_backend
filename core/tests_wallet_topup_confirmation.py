from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase

from core.models import (
    CustomUser,
    Driver,
    DriverWallet,
    WalletTopUp,
    WalletTransaction,
)
from core.services.wallet_service import (
    WalletNotActiveError,
)
from core.services.wallet_topup_service import (
    WalletTopUpConflictError,
    WalletTopUpConsistencyError,
    WalletTopUpStateError,
    confirm_wallet_topup,
    request_wallet_topup,
)


class WalletTopUpConfirmationTests(TestCase):

    def setUp(self):
        user = CustomUser.objects.create_user(
            email="confirm-topup@example.com",
            phone="+23567776001",
            user_type="driver",
        )

        self.driver = Driver.objects.create(
            user=user,
            is_enabled=True,
        )

        self.wallet = DriverWallet.objects.create(
            driver=self.driver,
            balance=Decimal("1000.00"),
        )

        self.topup, _ = request_wallet_topup(
            wallet=self.wallet,
            amount="10000.00",
            provider=WalletTopUp.Provider.AIRTEL_MONEY,
            phone="+23566000000",
            idempotency_key="request-confirm-001",
        )

    def confirm(self, **changes):
        values = {
            "topup_id": self.topup.pk,
            "provider": WalletTopUp.Provider.AIRTEL_MONEY,
            "provider_reference": "airtel-ref-001",
            "confirmed_amount": "10000.00",
        }
        values.update(changes)

        return confirm_wallet_topup(**values)

    def refresh(self):
        self.wallet.refresh_from_db()
        self.topup.refresh_from_db()

    def test_confirmation_credits_wallet(self):
        topup, tx, created = self.confirm()

        self.refresh()

        self.assertTrue(created)

        self.assertEqual(
            self.wallet.balance,
            Decimal("11000.00"),
        )

        self.assertEqual(
            topup.status,
            WalletTopUp.Status.SUCCESS,
        )

        self.assertEqual(
            tx.type,
            WalletTransaction.Type.TOPUP,
        )

        self.assertEqual(
            tx.direction,
            WalletTransaction.Direction.CREDIT,
        )

        self.assertEqual(
            tx.amount,
            Decimal("10000.00"),
        )

    def test_confirmation_marks_topup_success(self):
        self.confirm()
        self.refresh()

        self.assertEqual(
            self.topup.status,
            WalletTopUp.Status.SUCCESS,
        )

        self.assertEqual(
            self.topup.provider_reference,
            "airtel-ref-001",
        )

        self.assertIsNotNone(
            self.topup.confirmed_at
        )

        self.assertIsNone(
            self.topup.failure_reason
        )

    def test_confirmation_creates_exactly_one_ledger_entry(self):
        self.confirm()

        self.assertEqual(
            WalletTransaction.objects.count(),
            1,
        )

        tx = WalletTransaction.objects.get()

        self.assertEqual(
            tx.balance_before,
            Decimal("1000.00"),
        )

        self.assertEqual(
            tx.balance_after,
            Decimal("11000.00"),
        )

        self.assertEqual(
            tx.provider,
            WalletTopUp.Provider.AIRTEL_MONEY,
        )

        self.assertEqual(
            tx.provider_reference,
            "airtel-ref-001",
        )

    def test_duplicate_callback_is_idempotent(self):
        first_topup, first_tx, first_created = self.confirm()
        second_topup, second_tx, second_created = self.confirm()

        self.assertTrue(first_created)
        self.assertFalse(second_created)

        self.assertEqual(
            first_topup.pk,
            second_topup.pk,
        )

        self.assertEqual(
            first_tx.pk,
            second_tx.pk,
        )

        self.assertEqual(
            WalletTransaction.objects.count(),
            1,
        )

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("11000.00"),
        )

    def test_same_success_different_reference_conflicts(self):
        self.confirm()

        with self.assertRaises(
            WalletTopUpConflictError
        ):
            self.confirm(
                provider_reference="airtel-ref-OTHER"
            )

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("11000.00"),
        )

    def test_amount_mismatch_rejected_without_credit(self):
        with self.assertRaises(
            WalletTopUpConflictError
        ):
            self.confirm(
                confirmed_amount="9999.00"
            )

        self.refresh()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

        self.assertEqual(
            self.topup.status,
            WalletTopUp.Status.PENDING,
        )

        self.assertFalse(
            WalletTransaction.objects.exists()
        )

    def test_provider_mismatch_rejected_without_credit(self):
        with self.assertRaises(
            WalletTopUpConflictError
        ):
            self.confirm(
                provider=WalletTopUp.Provider.MOOV_MONEY
            )

        self.refresh()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

    def test_failed_topup_cannot_be_confirmed(self):
        WalletTopUp.objects.filter(
            pk=self.topup.pk
        ).update(
            status=WalletTopUp.Status.FAILED
        )

        with self.assertRaises(
            WalletTopUpStateError
        ):
            self.confirm()

    def test_cancelled_topup_cannot_be_confirmed(self):
        WalletTopUp.objects.filter(
            pk=self.topup.pk
        ).update(
            status=WalletTopUp.Status.CANCELLED
        )

        with self.assertRaises(
            WalletTopUpStateError
        ):
            self.confirm()

    def test_blocked_wallet_cannot_receive_first_confirmation(self):
        DriverWallet.objects.filter(
            pk=self.wallet.pk
        ).update(
            status=DriverWallet.Status.BLOCKED
        )

        with self.assertRaises(
            WalletNotActiveError
        ):
            self.confirm()

        self.refresh()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

        self.assertEqual(
            self.topup.status,
            WalletTopUp.Status.PENDING,
        )

    def test_duplicate_reference_from_other_topup_conflicts(self):
        other = WalletTopUp.objects.create(
            wallet=self.wallet,
            amount=Decimal("5000.00"),
            provider=WalletTopUp.Provider.AIRTEL_MONEY,
            phone="+23566000001",
            provider_reference="airtel-ref-001",
            idempotency_key="other-confirmed-topup",
            status=WalletTopUp.Status.SUCCESS,
        )

        self.assertIsNotNone(other.pk)

        with self.assertRaises(
            WalletTopUpConflictError
        ):
            self.confirm()

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

    def test_topup_save_failure_rolls_back_credit(self):
        real_save = WalletTopUp.save

        def fail_success(instance, *args, **kwargs):
            if instance.status == WalletTopUp.Status.SUCCESS:
                raise IntegrityError("forced topup save failure")

            return real_save(
                instance,
                *args,
                **kwargs,
            )

        with patch.object(
            WalletTopUp,
            "save",
            autospec=True,
            side_effect=fail_success,
        ):
            with self.assertRaises(
                IntegrityError
            ):
                self.confirm()

        self.refresh()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

        self.assertEqual(
            self.topup.status,
            WalletTopUp.Status.PENDING,
        )

        self.assertFalse(
            WalletTransaction.objects.exists()
        )

    def test_success_without_ledger_is_detected(self):
        WalletTopUp.objects.filter(
            pk=self.topup.pk
        ).update(
            status=WalletTopUp.Status.SUCCESS,
            provider_reference="airtel-ref-001",
        )

        with self.assertRaises(
            WalletTopUpConsistencyError
        ):
            self.confirm()

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

    def test_retry_after_wallet_becomes_blocked_still_reads_history(self):
        self.confirm()

        DriverWallet.objects.filter(
            pk=self.wallet.pk
        ).update(
            status=DriverWallet.Status.BLOCKED
        )

        topup, tx, created = self.confirm()

        self.assertFalse(created)
        self.assertEqual(
            topup.status,
            WalletTopUp.Status.SUCCESS,
        )
        self.assertEqual(
            tx.status,
            WalletTransaction.Status.SUCCESS,
        )

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("11000.00"),
        )
