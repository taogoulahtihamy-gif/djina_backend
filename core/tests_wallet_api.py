from decimal import Decimal

from django.urls import reverse

from rest_framework.test import APITestCase

from core.models import (
    Customer,
    CustomUser,
    Driver,
    DriverWallet,
    WalletTransaction,
)


class DriverWalletAPITests(APITestCase):

    def setUp(self):
        self.driver_user = CustomUser.objects.create_user(
            email="wallet-api-driver@example.com",
            phone="+23567774001",
            user_type="driver",
        )

        self.driver = Driver.objects.create(
            user=self.driver_user,
            is_enabled=True,
        )

        self.other_driver_user = CustomUser.objects.create_user(
            email="wallet-api-other@example.com",
            phone="+23567774002",
            user_type="driver",
        )

        self.other_driver = Driver.objects.create(
            user=self.other_driver_user,
            is_enabled=True,
        )

        self.customer_user = CustomUser.objects.create_user(
            email="wallet-api-customer@example.com",
            phone="+23567774003",
            user_type="customer",
        )

        Customer.objects.create(
            user=self.customer_user
        )

    def wallet_url(self):
        return reverse("wallet-list")

    def transactions_url(self):
        return reverse("wallet-transactions")

    def authenticate_driver(self):
        self.client.force_authenticate(
            self.driver_user
        )

    def test_wallet_get_creates_empty_wallet(self):
        self.authenticate_driver()

        self.assertFalse(
            DriverWallet.objects.filter(
                driver=self.driver
            ).exists()
        )

        response = self.client.get(
            self.wallet_url()
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        wallet = DriverWallet.objects.get(
            driver=self.driver
        )

        self.assertEqual(
            wallet.balance,
            Decimal("0.00"),
        )

        self.assertEqual(
            response.data["balance"],
            "0.00",
        )

        self.assertEqual(
            response.data["reserved_balance"],
            "0.00",
        )

        self.assertEqual(
            response.data["available_balance"],
            "0.00",
        )

        self.assertEqual(
            response.data["currency"],
            "XAF",
        )

        self.assertEqual(
            response.data["status"],
            DriverWallet.Status.ACTIVE,
        )

    def test_wallet_returns_real_balances(self):
        DriverWallet.objects.create(
            driver=self.driver,
            balance=Decimal("10000.00"),
            reserved_balance=Decimal("300.00"),
        )

        self.authenticate_driver()

        response = self.client.get(
            self.wallet_url()
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data["balance"],
            "10000.00",
        )

        self.assertEqual(
            response.data["reserved_balance"],
            "300.00",
        )

        self.assertEqual(
            response.data["available_balance"],
            "9700.00",
        )

    def test_driver_never_sees_other_wallet(self):
        DriverWallet.objects.create(
            driver=self.driver,
            balance=Decimal("1000.00"),
        )

        DriverWallet.objects.create(
            driver=self.other_driver,
            balance=Decimal("999999.00"),
        )

        self.authenticate_driver()

        response = self.client.get(
            self.wallet_url()
        )

        self.assertEqual(
            response.data["balance"],
            "1000.00",
        )

    def test_transactions_only_return_own_wallet(self):
        wallet = DriverWallet.objects.create(
            driver=self.driver,
            balance=Decimal("1000.00"),
        )

        other_wallet = DriverWallet.objects.create(
            driver=self.other_driver,
            balance=Decimal("5000.00"),
        )

        own = WalletTransaction.objects.create(
            wallet=wallet,
            type=WalletTransaction.Type.TOPUP,
            direction=WalletTransaction.Direction.CREDIT,
            amount=Decimal("1000.00"),
            balance_before=Decimal("0.00"),
            balance_after=Decimal("1000.00"),
            idempotency_key="wallet-api-own",
            status=WalletTransaction.Status.SUCCESS,
        )

        WalletTransaction.objects.create(
            wallet=other_wallet,
            type=WalletTransaction.Type.TOPUP,
            direction=WalletTransaction.Direction.CREDIT,
            amount=Decimal("5000.00"),
            balance_before=Decimal("0.00"),
            balance_after=Decimal("5000.00"),
            idempotency_key="wallet-api-other",
            status=WalletTransaction.Status.SUCCESS,
        )

        self.authenticate_driver()

        response = self.client.get(
            self.transactions_url()
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        data = (
            response.data["results"]
            if isinstance(response.data, dict)
            and "results" in response.data
            else response.data
        )

        self.assertEqual(len(data), 1)

        self.assertEqual(
            data[0]["id"],
            own.pk,
        )

    def test_transactions_newest_first(self):
        wallet = DriverWallet.objects.create(
            driver=self.driver,
            balance=Decimal("1000.00"),
        )

        first = WalletTransaction.objects.create(
            wallet=wallet,
            type=WalletTransaction.Type.TOPUP,
            direction=WalletTransaction.Direction.CREDIT,
            amount=Decimal("1000.00"),
            balance_before=Decimal("0.00"),
            balance_after=Decimal("1000.00"),
            idempotency_key="wallet-api-first",
            status=WalletTransaction.Status.SUCCESS,
        )

        second = WalletTransaction.objects.create(
            wallet=wallet,
            type=WalletTransaction.Type.COMMISSION,
            direction=WalletTransaction.Direction.DEBIT,
            amount=Decimal("100.00"),
            balance_before=Decimal("1000.00"),
            balance_after=Decimal("900.00"),
            idempotency_key="wallet-api-second",
            status=WalletTransaction.Status.SUCCESS,
        )

        self.authenticate_driver()

        response = self.client.get(
            self.transactions_url()
        )

        data = (
            response.data["results"]
            if isinstance(response.data, dict)
            and "results" in response.data
            else response.data
        )

        self.assertEqual(
            [item["id"] for item in data],
            [second.pk, first.pk],
        )

    def test_wallet_is_read_only(self):
        self.authenticate_driver()

        response = self.client.post(
            self.wallet_url(),
            {
                "balance": "999999.00",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            405,
        )

    def test_customer_cannot_access_wallet(self):
        self.client.force_authenticate(
            self.customer_user
        )

        response = self.client.get(
            self.wallet_url()
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_customer_cannot_access_transactions(self):
        self.client.force_authenticate(
            self.customer_user
        )

        response = self.client.get(
            self.transactions_url()
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_unauthenticated_user_cannot_access_wallet(self):
        response = self.client.get(
            self.wallet_url()
        )

        self.assertIn(
            response.status_code,
            (401, 403),
        )

    def test_blocked_wallet_remains_readable(self):
        DriverWallet.objects.create(
            driver=self.driver,
            balance=Decimal("800.00"),
            status=DriverWallet.Status.BLOCKED,
        )

        self.authenticate_driver()

        response = self.client.get(
            self.wallet_url()
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data["status"],
            DriverWallet.Status.BLOCKED,
        )

        self.assertEqual(
            response.data["balance"],
            "800.00",
        )

    def test_closed_wallet_remains_readable(self):
        DriverWallet.objects.create(
            driver=self.driver,
            balance=Decimal("700.00"),
            status=DriverWallet.Status.CLOSED,
        )

        self.authenticate_driver()

        response = self.client.get(
            self.wallet_url()
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data["status"],
            DriverWallet.Status.CLOSED,
        )
