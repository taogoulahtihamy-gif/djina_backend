from decimal import Decimal

from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import (
    Customer,
    CustomUser,
    Driver,
    DriverWallet,
    WalletTopUp,
    WalletTransaction,
)


class DriverWalletTopUpAPITests(APITestCase):

    def setUp(self):
        self.driver_user = CustomUser.objects.create_user(
            email="topup-driver@example.com",
            phone="+23567775001",
            user_type="driver",
        )
        self.driver = Driver.objects.create(
            user=self.driver_user,
            is_enabled=True,
        )
        self.wallet = DriverWallet.objects.create(
            driver=self.driver,
            balance=Decimal("1000.00"),
        )

        self.other_user = CustomUser.objects.create_user(
            email="topup-other@example.com",
            phone="+23567775002",
            user_type="driver",
        )
        self.other_driver = Driver.objects.create(
            user=self.other_user,
            is_enabled=True,
        )
        self.other_wallet = DriverWallet.objects.create(
            driver=self.other_driver,
            balance=Decimal("5000.00"),
        )

        self.customer_user = CustomUser.objects.create_user(
            email="topup-customer@example.com",
            phone="+23567775003",
            user_type="customer",
        )
        Customer.objects.create(
            user=self.customer_user
        )

    def url(self):
        return reverse("wallet-topups")

    def auth(self, user=None):
        self.client.force_authenticate(
            user or self.driver_user
        )

    def payload(self, **changes):
        data = {
            "amount": "10000.00",
            "provider": WalletTopUp.Provider.AIRTEL_MONEY,
            "phone": "+23566000000",
            "idempotency_key": "topup-test-001",
        }
        data.update(changes)
        return data

    def test_create_pending_topup(self):
        self.auth()

        response = self.client.post(
            self.url(),
            self.payload(),
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        topup = WalletTopUp.objects.get()

        self.assertEqual(
            topup.status,
            WalletTopUp.Status.PENDING,
        )
        self.assertEqual(
            topup.amount,
            Decimal("10000.00"),
        )
        self.assertEqual(topup.currency, "XAF")
        self.assertIsNone(topup.provider_reference)
        self.assertIsNone(topup.confirmed_at)

    def test_topup_request_never_credits_wallet(self):
        before = self.wallet.balance
        self.auth()

        self.client.post(
            self.url(),
            self.payload(),
            format="json",
        )

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            before,
        )
        self.assertFalse(
            WalletTransaction.objects.exists()
        )

    def test_client_cannot_force_success_or_reference(self):
        self.auth()

        response = self.client.post(
            self.url(),
            self.payload(
                status="success",
                provider_reference="fake-provider-ref",
                confirmed_at="2026-01-01T00:00:00Z",
            ),
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        topup = WalletTopUp.objects.get()

        self.assertEqual(
            topup.status,
            WalletTopUp.Status.PENDING,
        )
        self.assertIsNone(
            topup.provider_reference
        )
        self.assertIsNone(
            topup.confirmed_at
        )

    def test_identical_retry_is_idempotent(self):
        self.auth()

        first = self.client.post(
            self.url(),
            self.payload(),
            format="json",
        )

        second = self.client.post(
            self.url(),
            self.payload(),
            format="json",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)

        self.assertEqual(
            WalletTopUp.objects.count(),
            1,
        )

        self.assertEqual(
            first.data["id"],
            second.data["id"],
        )

    def test_same_key_different_amount_conflicts(self):
        self.auth()

        self.client.post(
            self.url(),
            self.payload(),
            format="json",
        )

        response = self.client.post(
            self.url(),
            self.payload(amount="12000.00"),
            format="json",
        )

        self.assertEqual(
            response.status_code,
            409,
        )

    def test_same_key_different_provider_conflicts(self):
        self.auth()

        self.client.post(
            self.url(),
            self.payload(),
            format="json",
        )

        response = self.client.post(
            self.url(),
            self.payload(
                provider=WalletTopUp.Provider.MOOV_MONEY
            ),
            format="json",
        )

        self.assertEqual(response.status_code, 409)

    def test_global_key_cannot_be_reused_by_other_driver(self):
        self.auth()

        self.client.post(
            self.url(),
            self.payload(),
            format="json",
        )

        self.auth(self.other_user)

        response = self.client.post(
            self.url(),
            self.payload(),
            format="json",
        )

        self.assertEqual(response.status_code, 409)

        self.assertFalse(
            WalletTopUp.objects.filter(
                wallet=self.other_wallet
            ).exists()
        )

    def test_blocked_wallet_cannot_create_new_topup(self):
        DriverWallet.objects.filter(
            pk=self.wallet.pk
        ).update(
            status=DriverWallet.Status.BLOCKED
        )

        self.auth()

        response = self.client.post(
            self.url(),
            self.payload(),
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(WalletTopUp.objects.exists())

    def test_closed_wallet_cannot_create_new_topup(self):
        DriverWallet.objects.filter(
            pk=self.wallet.pk
        ).update(
            status=DriverWallet.Status.CLOSED
        )

        self.auth()

        response = self.client.post(
            self.url(),
            self.payload(),
            format="json",
        )

        self.assertEqual(response.status_code, 409)

    def test_retry_existing_topup_after_wallet_blocked(self):
        self.auth()

        first = self.client.post(
            self.url(),
            self.payload(),
            format="json",
        )

        DriverWallet.objects.filter(
            pk=self.wallet.pk
        ).update(
            status=DriverWallet.Status.BLOCKED
        )

        second = self.client.post(
            self.url(),
            self.payload(),
            format="json",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            WalletTopUp.objects.count(),
            1,
        )

    def test_zero_amount_rejected(self):
        self.auth()

        response = self.client.post(
            self.url(),
            self.payload(amount="0.00"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_negative_amount_rejected(self):
        self.auth()

        response = self.client.post(
            self.url(),
            self.payload(amount="-1.00"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_fractional_cent_rejected(self):
        self.auth()

        response = self.client.post(
            self.url(),
            self.payload(amount="10.001"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_invalid_provider_rejected(self):
        self.auth()

        response = self.client.post(
            self.url(),
            self.payload(provider="fake_money"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_list_only_own_topups(self):
        WalletTopUp.objects.create(
            wallet=self.wallet,
            amount=Decimal("1000.00"),
            provider=WalletTopUp.Provider.AIRTEL_MONEY,
            phone="+23566000000",
            idempotency_key="own-topup",
        )

        WalletTopUp.objects.create(
            wallet=self.other_wallet,
            amount=Decimal("5000.00"),
            provider=WalletTopUp.Provider.MOOV_MONEY,
            phone="+23566000001",
            idempotency_key="other-topup",
        )

        self.auth()

        response = self.client.get(
            self.url()
        )

        self.assertEqual(response.status_code, 200)

        data = (
            response.data["results"]
            if isinstance(response.data, dict)
            and "results" in response.data
            else response.data
        )

        self.assertEqual(len(data), 1)
        self.assertEqual(
            data[0]["idempotency_key"],
            "own-topup",
        )

    def test_customer_forbidden(self):
        self.auth(self.customer_user)

        response = self.client.get(
            self.url()
        )

        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_forbidden(self):
        response = self.client.get(
            self.url()
        )

        self.assertIn(
            response.status_code,
            (401, 403),
        )
