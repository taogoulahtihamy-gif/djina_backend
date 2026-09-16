from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core.models import (
    CustomUser,
    Driver,
    DriverWallet,
    WalletProviderEvent,
    WalletTopUp,
    WalletTransaction,
)
from core.services.wallet_provider_event_service import (
    process_journaled_wallet_provider_callback,
)
from core.services.wallet_topup_service import (
    WalletTopUpConflictError,
    WalletTopUpError,
)


class WalletProviderEventJournalTests(
    TestCase
):

    def setUp(self):
        user = CustomUser.objects.create_user(
            email="provider-event@example.com",
            phone="+23567779501",
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
        suffix,
    ):
        return WalletTopUp.objects.create(
            wallet=self.wallet,
            amount=Decimal("5000.00"),
            currency="XAF",
            provider=(
                WalletTopUp
                .Provider
                .AIRTEL_MONEY
            ),
            phone="+23566000300",
            provider_reference=(
                f"journal-{suffix}"
            ),
            provider_status="pending",
            initiated_at=timezone.now(),
            idempotency_key=(
                f"journal-request-{suffix}"
            ),
            status=(
                WalletTopUp
                .Status
                .PENDING
            ),
        )

    def test_success_callback_creates_accepted_event(self):
        topup = self.create_topup(
            "success"
        )

        result = (
            process_journaled_wallet_provider_callback(
                topup_id=topup.pk,
                provider=topup.provider,
                provider_reference=
                    topup.provider_reference,
                amount=topup.amount,
                callback_status=
                    WalletTopUp.Status.SUCCESS,
                provider_status="COMPLETED",
            )
        )

        event = (
            WalletProviderEvent.objects.get()
        )

        self.assertEqual(
            event.outcome,
            WalletProviderEvent.Outcome.ACCEPTED,
        )

        self.assertTrue(
            event.processed
        )

        self.assertEqual(
            event.topup_id,
            topup.pk,
        )

        self.assertEqual(
            event.reported_topup_id,
            topup.pk,
        )

        self.assertEqual(
            event.provider_status,
            "COMPLETED",
        )

        self.assertEqual(
            event.wallet_transaction_id,
            result.wallet_transaction.pk,
        )

        self.assertIsNotNone(
            event.completed_at
        )

    def test_duplicate_success_creates_second_event_without_second_credit(self):
        topup = self.create_topup(
            "duplicate"
        )

        kwargs = {
            "topup_id": topup.pk,
            "provider": topup.provider,
            "provider_reference":
                topup.provider_reference,
            "amount": topup.amount,
            "callback_status":
                WalletTopUp.Status.SUCCESS,
            "provider_status":
                "COMPLETED",
        }

        first = (
            process_journaled_wallet_provider_callback(
                **kwargs
            )
        )

        second = (
            process_journaled_wallet_provider_callback(
                **kwargs
            )
        )

        self.wallet.refresh_from_db()

        events = (
            WalletProviderEvent.objects
            .order_by("created_at", "pk")
        )

        self.assertEqual(
            events.count(),
            2,
        )

        self.assertTrue(
            events[0].processed
        )

        self.assertFalse(
            events[1].processed
        )

        self.assertEqual(
            first.wallet_transaction.pk,
            second.wallet_transaction.pk,
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

    def test_failed_callback_is_journaled_without_money(self):
        topup = self.create_topup(
            "failed"
        )

        process_journaled_wallet_provider_callback(
            topup_id=topup.pk,
            provider=topup.provider,
            provider_reference=
                topup.provider_reference,
            amount=topup.amount,
            callback_status=
                WalletTopUp.Status.FAILED,
            provider_status="DECLINED",
            failure_reason=
                "Insufficient funds.",
        )

        event = (
            WalletProviderEvent.objects.get()
        )

        self.wallet.refresh_from_db()

        self.assertEqual(
            event.outcome,
            WalletProviderEvent.Outcome.ACCEPTED,
        )

        self.assertTrue(
            event.processed
        )

        self.assertIsNone(
            event.wallet_transaction_id
        )

        self.assertEqual(
            event.failure_reason,
            "Insufficient funds.",
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

    def test_wrong_amount_is_journaled_as_rejected(self):
        topup = self.create_topup(
            "wrong-amount"
        )

        with self.assertRaises(
            WalletTopUpError
        ):
            process_journaled_wallet_provider_callback(
                topup_id=topup.pk,
                provider=topup.provider,
                provider_reference=
                    topup.provider_reference,
                amount=Decimal("9999.00"),
                callback_status=
                    WalletTopUp.Status.SUCCESS,
            )

        event = (
            WalletProviderEvent.objects.get()
        )

        self.wallet.refresh_from_db()

        self.assertEqual(
            event.outcome,
            WalletProviderEvent.Outcome.REJECTED,
        )

        self.assertFalse(
            event.processed
        )

        self.assertTrue(
            event.error_type
        )

        self.assertTrue(
            event.error_message
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

        self.assertFalse(
            WalletTransaction.objects.exists()
        )

    def test_unknown_topup_is_still_journaled(self):
        unknown_id = 999999

        with self.assertRaises(
            WalletTopUpError
        ):
            process_journaled_wallet_provider_callback(
                topup_id=unknown_id,
                provider=(
                    WalletTopUp
                    .Provider
                    .AIRTEL_MONEY
                ),
                provider_reference=
                    "unknown-provider-ref",
                amount=Decimal("5000.00"),
                callback_status=
                    WalletTopUp.Status.SUCCESS,
            )

        event = (
            WalletProviderEvent.objects.get()
        )

        self.assertIsNone(
            event.topup_id
        )

        self.assertEqual(
            event.reported_topup_id,
            unknown_id,
        )

        self.assertEqual(
            event.outcome,
            WalletProviderEvent.Outcome.REJECTED,
        )

    def test_conflicting_terminal_retry_is_journaled_as_rejected(self):
        topup = self.create_topup(
            "terminal-conflict"
        )

        process_journaled_wallet_provider_callback(
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
            process_journaled_wallet_provider_callback(
                topup_id=topup.pk,
                provider=topup.provider,
                provider_reference=
                    topup.provider_reference,
                amount=topup.amount,
                callback_status=
                    WalletTopUp.Status.SUCCESS,
                provider_status="REVERSED",
            )

        events = (
            WalletProviderEvent.objects
            .order_by("created_at", "pk")
        )

        self.assertEqual(
            events.count(),
            2,
        )

        self.assertEqual(
            events[0].outcome,
            WalletProviderEvent.Outcome.ACCEPTED,
        )

        self.assertEqual(
            events[1].outcome,
            WalletProviderEvent.Outcome.REJECTED,
        )

        self.wallet.refresh_from_db()

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
