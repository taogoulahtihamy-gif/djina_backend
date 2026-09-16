from decimal import Decimal

from django.test import TestCase

from core.models import (
    CustomUser,
    Driver,
    DriverWallet,
    WalletTopUp,
    WalletTransaction,
)
from core.services.wallet_provider_callback_service import (
    WalletProviderCallbackError,
    process_wallet_provider_callback,
)
from core.services.wallet_topup_service import (
    WalletTopUpStateError,
    request_wallet_topup,
)


class WalletProviderCallbackServiceTests(TestCase):

    def setUp(self):
        user = CustomUser.objects.create_user(
            email="callback-service@example.com",
            phone="+23567778001",
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
            idempotency_key="callback-service-001",
        )

    def values(self, **changes):
        data = {
            "topup_id": self.topup.pk,
            "provider":
                WalletTopUp.Provider.AIRTEL_MONEY,
            "provider_reference":
                "callback-ref-001",
            "amount": "10000.00",
            "callback_status":
                WalletTopUp.Status.SUCCESS,
            "failure_reason": None,
        }

        data.update(changes)
        return data

    def test_success_callback_credits_wallet(self):
        result = process_wallet_provider_callback(
            **self.values()
        )

        self.wallet.refresh_from_db()
        self.topup.refresh_from_db()

        self.assertTrue(result.processed)

        self.assertIsNotNone(
            result.wallet_transaction
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("11000.00"),
        )

        self.assertEqual(
            self.topup.status,
            WalletTopUp.Status.SUCCESS,
        )

    def test_duplicate_success_is_idempotent(self):
        first = process_wallet_provider_callback(
            **self.values()
        )

        second = process_wallet_provider_callback(
            **self.values()
        )

        self.assertTrue(first.processed)
        self.assertFalse(second.processed)

        self.assertEqual(
            first.wallet_transaction.pk,
            second.wallet_transaction.pk,
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

    def test_failed_callback_does_not_credit(self):
        result = process_wallet_provider_callback(
            **self.values(
                callback_status=
                    WalletTopUp.Status.FAILED,
                failure_reason=
                    "provider_declined",
            )
        )

        self.wallet.refresh_from_db()
        self.topup.refresh_from_db()

        self.assertTrue(result.processed)

        self.assertIsNone(
            result.wallet_transaction
        )

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

        self.assertEqual(
            self.topup.status,
            WalletTopUp.Status.FAILED,
        )

        self.assertFalse(
            WalletTransaction.objects.exists()
        )

    def test_duplicate_failure_is_idempotent(self):
        values = self.values(
            callback_status=
                WalletTopUp.Status.FAILED,
            failure_reason=
                "provider_declined",
        )

        first = process_wallet_provider_callback(
            **values
        )

        second = process_wallet_provider_callback(
            **values
        )

        self.assertTrue(first.processed)
        self.assertFalse(second.processed)

        self.assertFalse(
            WalletTransaction.objects.exists()
        )

    def test_success_with_failure_reason_rejected(self):
        with self.assertRaises(
            WalletProviderCallbackError
        ):
            process_wallet_provider_callback(
                **self.values(
                    failure_reason=
                        "should-not-exist",
                )
            )

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

    def test_failed_without_reason_rejected(self):
        with self.assertRaises(
            WalletProviderCallbackError
        ):
            process_wallet_provider_callback(
                **self.values(
                    callback_status=
                        WalletTopUp.Status.FAILED,
                    failure_reason=None,
                )
            )

        self.assertFalse(
            WalletTransaction.objects.exists()
        )

    def test_unknown_status_rejected(self):
        with self.assertRaises(
            WalletProviderCallbackError
        ):
            process_wallet_provider_callback(
                **self.values(
                    callback_status="unknown",
                )
            )

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

    def test_failed_then_success_remains_forbidden(self):
        process_wallet_provider_callback(
            **self.values(
                callback_status=
                    WalletTopUp.Status.FAILED,
                failure_reason=
                    "provider_declined",
            )
        )

        with self.assertRaises(
            WalletTopUpStateError
        ):
            process_wallet_provider_callback(
                **self.values()
            )

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )
