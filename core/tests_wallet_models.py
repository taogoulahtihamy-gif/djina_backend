from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from core.models import (
    Commission, CommissionReservation, Course, Customer, CustomUser, Driver,
    DriverWallet, WalletTopUp, WalletTransaction,
)


class WalletModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        driver_user = CustomUser.objects.create_user(
            email="wallet-driver@example.com", phone="+23560000101", user_type="driver",
        )
        customer_user = CustomUser.objects.create_user(
            email="wallet-customer@example.com", phone="+23560000102",
        )
        cls.driver = Driver.objects.create(user=driver_user)
        cls.customer = Customer.objects.create(user=customer_user)
        cls.wallet = DriverWallet.objects.create(
            driver=cls.driver, balance=Decimal("1000.00"), reserved_balance=Decimal("200.00"),
        )
        cls.course = Course.objects.create(
            driver=cls.driver, customer=cls.customer,
            departure_latitude=Decimal("12.100000"), departure_longitude=Decimal("15.100000"),
            destination_latitude=Decimal("12.200000"), destination_longitude=Decimal("15.200000"),
            starting_landmark="Chagoua", arrival_landmark="Farcha",
            initial_price=Decimal("3800.00"), final_price=Decimal("3800.00"),
        )

    def make_transaction(self, **overrides):
        values = dict(
            wallet=self.wallet, type=WalletTransaction.Type.TOPUP,
            direction=WalletTransaction.Direction.CREDIT, amount=Decimal("100.00"),
            balance_before=Decimal("1000.00"), balance_after=Decimal("1100.00"),
            idempotency_key="transaction-1",
        )
        values.update(overrides)
        return WalletTransaction.objects.create(**values)

    def make_topup(self, **overrides):
        values = dict(
            wallet=self.wallet, amount=Decimal("100.00"),
            provider=WalletTopUp.Provider.AIRTEL_MONEY, phone="+23560000101",
            idempotency_key="topup-1",
        )
        values.update(overrides)
        return WalletTopUp.objects.create(**values)

    def make_reservation(self, **overrides):
        values = dict(
            wallet=self.wallet, course=self.course, driver=self.driver,
            estimated_amount=Decimal("570.00"),
        )
        values.update(overrides)
        return CommissionReservation.objects.create(**values)

    def assert_db_rejects(self, operation, **values):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                operation(**values)

    def test_one_wallet_per_driver(self):
        self.assert_db_rejects(DriverWallet.objects.create, driver=self.driver)

    def test_negative_balance_rejected(self):
        self.assert_db_rejects(
            DriverWallet.objects.filter(pk=self.wallet.pk).update,
            balance=Decimal("-0.01"), reserved_balance=Decimal("0.00"),
        )

    def test_negative_reserved_balance_rejected(self):
        self.assert_db_rejects(
            DriverWallet.objects.filter(pk=self.wallet.pk).update, reserved_balance=Decimal("-0.01"),
        )

    def test_reserved_balance_above_balance_rejected(self):
        self.assert_db_rejects(
            DriverWallet.objects.filter(pk=self.wallet.pk).update, reserved_balance=Decimal("1000.01"),
        )

    def test_available_balance_is_exact_decimal_and_read_only(self):
        self.assertEqual(self.wallet.available_balance, Decimal("800.00"))
        self.assertIsInstance(self.wallet.available_balance, Decimal)
        with self.assertRaises(AttributeError):
            self.wallet.available_balance = Decimal("0.00")

    def test_zero_balances_and_fully_reserved_balance_allowed(self):
        for value in (Decimal("0.00"), Decimal("1000.00")):
            with self.subTest(value=value):
                DriverWallet.objects.filter(pk=self.wallet.pk).update(balance=value, reserved_balance=value)
                self.wallet.refresh_from_db()
                self.assertEqual(self.wallet.available_balance, Decimal("0.00"))

    def test_transaction_non_positive_amount_rejected(self):
        for amount in (Decimal("0.00"), Decimal("-0.01")):
            with self.subTest(amount=amount):
                self.assert_db_rejects(self.make_transaction, amount=amount)

    def test_transaction_idempotency_key_unique(self):
        self.make_transaction()
        self.assert_db_rejects(self.make_transaction)

    def test_transaction_negative_balance_before_rejected(self):
        self.assert_db_rejects(self.make_transaction, balance_before=Decimal("-0.01"))

    def test_transaction_negative_balance_after_rejected(self):
        self.assert_db_rejects(self.make_transaction, balance_after=Decimal("-0.01"))

    def test_topup_non_positive_amount_rejected(self):
        for amount in (Decimal("0.00"), Decimal("-0.01")):
            with self.subTest(amount=amount):
                self.assert_db_rejects(self.make_topup, amount=amount)

    def test_topup_idempotency_key_unique(self):
        self.make_topup()
        self.assert_db_rejects(self.make_topup)

    def test_topup_provider_reference_unique_for_same_provider(self):
        self.make_topup(provider_reference="reference-1")
        self.assert_db_rejects(self.make_topup, idempotency_key="topup-2", provider_reference="reference-1")

    def test_topup_same_reference_allowed_for_different_providers(self):
        self.make_topup(provider_reference="reference-1")
        self.make_topup(
            idempotency_key="topup-2", provider_reference="reference-1", provider=WalletTopUp.Provider.MOOV_MONEY,
        )
        self.assertEqual(WalletTopUp.objects.count(), 2)

    def test_topup_multiple_null_references_allowed(self):
        self.make_topup(provider_reference=None)
        self.make_topup(idempotency_key="topup-2", provider_reference=None)
        self.assertEqual(WalletTopUp.objects.filter(provider_reference__isnull=True).count(), 2)

    def test_topup_multiple_empty_references_allowed(self):
        self.make_topup(provider_reference="")
        self.make_topup(idempotency_key="topup-2", provider_reference="")
        self.assertEqual(WalletTopUp.objects.filter(provider_reference="").count(), 2)

    def test_pending_topup_does_not_change_wallet(self):
        topup = self.make_topup()
        self.assertEqual(topup.status, WalletTopUp.Status.PENDING)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("1000.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("200.00"))
        self.assertFalse(self.wallet.transactions.exists())

    def test_successful_topup_creation_does_not_credit_wallet(self):
        self.make_topup(status=WalletTopUp.Status.SUCCESS)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("1000.00"))
        self.assertFalse(self.wallet.transactions.exists())

    def test_one_reservation_per_course(self):
        self.make_reservation()
        self.assert_db_rejects(self.make_reservation)

    def test_reservation_non_positive_amount_rejected(self):
        for amount in (Decimal("0.00"), Decimal("-0.01")):
            with self.subTest(amount=amount):
                self.assert_db_rejects(self.make_reservation, estimated_amount=amount)

    def test_financial_records_do_not_change_balances_or_commission_creation(self):
        reservation = self.make_reservation()
        self.make_transaction()
        self.make_topup()
        self.course.status = Course.Status.COMPLETED
        self.course.save()
        self.course.save()
        commission = Commission.objects.get(course=self.course)
        self.assertEqual(commission.commission_amount, Decimal("570.00"))
        self.assertEqual(commission.driver_net_amount, Decimal("3230.00"))
        self.assertEqual(commission.status, Commission.Status.PENDING)
        self.wallet.refresh_from_db()
        reservation.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("1000.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("200.00"))
        self.assertEqual(reservation.status, CommissionReservation.Status.ACTIVE)
        self.assertEqual(self.wallet.transactions.count(), 1)

    def test_financial_foreign_keys_protect_related_records(self):
        self.course.status = Course.Status.COMPLETED
        self.course.save()
        commission = Commission.objects.get(course=self.course)
        self.make_transaction(course=self.course, commission=commission)
        self.make_topup()
        self.make_reservation()
        for record in (self.driver, self.wallet, self.course, commission):
            with self.subTest(model=type(record).__name__):
                with self.assertRaises(ProtectedError):
                    with transaction.atomic():
                        record.delete()
