"""Contraintes DB de transition 3C-0 et compatibilité du service legacy."""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from core.models import (
    Commission, CommissionReservation, CommissionSettlement, Course, Customer,
    CustomUser, Driver, DriverWallet, WalletTransaction,
)
from core.services.commission_service import confirm_commission_settlement
from core.services.wallet_service import reserve_commission


class WalletTransitionModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = CustomUser.objects.create_user(
            email="transition-driver@example.com", phone="+23560000201", user_type="driver",
        )
        cls.driver = Driver.objects.create(user=user)
        customer_user = CustomUser.objects.create_user(
            email="transition-customer@example.com", phone="+23560000202",
        )
        customer = Customer.objects.create(user=customer_user)
        cls.admin = CustomUser.objects.create_user(
            email="transition-admin@example.com", phone="+23560000203", is_superuser=True,
        )
        cls.course = Course.objects.create(
            driver=cls.driver, customer=customer,
            departure_latitude=Decimal("12.1"), departure_longitude=Decimal("15.1"),
            destination_latitude=Decimal("12.2"), destination_longitude=Decimal("15.2"),
            starting_landmark="Chagoua", arrival_landmark="Farcha",
            initial_price=Decimal("1000.00"),
        )
        cls.wallet = DriverWallet.objects.create(driver=cls.driver, balance=Decimal("1000.00"))
        # Journal de fixture uniquement : aucun appel de débit/crédit.
        cls.wallet_transaction = WalletTransaction.objects.create(
            wallet=cls.wallet, type=WalletTransaction.Type.COMMISSION,
            direction=WalletTransaction.Direction.DEBIT, status=WalletTransaction.Status.SUCCESS,
            amount=Decimal("150.00"), balance_before=Decimal("1000.00"),
            balance_after=Decimal("850.00"), idempotency_key="transition-fixture",
        )

    def reservation(self, **overrides):
        values = dict(wallet=self.wallet, driver=self.driver, course=self.course,
                      estimated_amount=Decimal("150.00"))
        values.update(overrides)
        return CommissionReservation.objects.create(**values)

    def settlement(self, **overrides):
        values = dict(driver=self.driver, total_amount=Decimal("150.00"),
                      payment_mode=CommissionSettlement.PaymentMode.CASH,
                      confirmed_by=self.admin, paid_at=timezone.now())
        values.update(overrides)
        return CommissionSettlement.objects.create(**values)

    def wallet_settlement(self, **overrides):
        values = dict(payment_mode=CommissionSettlement.PaymentMode.WALLET,
                      confirmed_by=None, wallet_transaction=self.wallet_transaction)
        values.update(overrides)
        return self.settlement(**values)

    def commission(self, **overrides):
        values = dict(course=self.course, driver=self.driver, gross_amount=Decimal("1000.00"),
                      commission_rate=Decimal("15.00"), commission_amount=Decimal("150.00"),
                      driver_net_amount=Decimal("850.00"))
        values.update(overrides)
        return Commission.objects.create(**values)

    def assert_db_rejects(self, operation, **values):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                operation(**values)

    def test_snapshot_both_null_allowed(self):
        result = self.reservation()
        result.refresh_from_db()
        self.assertIsNone(result.gross_amount)
        self.assertIsNone(result.commission_rate)

    def test_snapshot_complete_allowed(self):
        result = self.reservation(gross_amount=Decimal("1000.00"), commission_rate=Decimal("15.00"))
        result.refresh_from_db()
        self.assertEqual(result.gross_amount, Decimal("1000.00"))
        self.assertEqual(result.commission_rate, Decimal("15.00"))

    def test_snapshot_gross_only_rejected(self):
        self.assert_db_rejects(self.reservation, gross_amount=Decimal("1000.00"))

    def test_snapshot_rate_only_rejected(self):
        self.assert_db_rejects(self.reservation, commission_rate=Decimal("15.00"))

    def test_snapshot_negative_gross_rejected(self):
        self.assert_db_rejects(self.reservation, gross_amount=Decimal("-0.01"), commission_rate=Decimal("15"))

    def test_snapshot_zero_gross_allowed(self):
        self.assertIsNotNone(self.reservation(gross_amount=Decimal("0"), commission_rate=Decimal("15")).pk)

    def test_snapshot_negative_rate_rejected(self):
        self.assert_db_rejects(self.reservation, gross_amount=Decimal("1000"), commission_rate=Decimal("-0.01"))

    def test_snapshot_rate_above_100_rejected(self):
        self.assert_db_rejects(self.reservation, gross_amount=Decimal("1000"), commission_rate=Decimal("100.01"))

    def test_snapshot_zero_rate_allowed(self):
        self.assertIsNotNone(self.reservation(gross_amount=Decimal("1000"), commission_rate=Decimal("0")).pk)

    def test_snapshot_100_rate_allowed(self):
        self.assertIsNotNone(self.reservation(gross_amount=Decimal("1000"), commission_rate=Decimal("100")).pk)

    def test_legacy_reserve_keeps_snapshots_null_during_transition(self):
        result = reserve_commission(wallet=self.wallet, driver=self.driver, course=self.course,
                                    estimated_amount=Decimal("150"))
        result.refresh_from_db()
        self.assertIsNone(result.gross_amount)
        self.assertIsNone(result.commission_rate)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("1000"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("150"))
        self.assertEqual(WalletTransaction.objects.count(), 1)

    def assert_manual_allowed(self, mode):
        result = self.settlement(payment_mode=mode)
        result.refresh_from_db()
        self.assertEqual(result.payment_mode, mode)
        self.assertEqual(result.confirmed_by_id, self.admin.pk)
        self.assertIsNone(result.wallet_transaction_id)

    def test_manual_cash_allowed(self):
        self.assert_manual_allowed(CommissionSettlement.PaymentMode.CASH)

    def test_manual_airtel_allowed(self):
        self.assert_manual_allowed(CommissionSettlement.PaymentMode.AIRTEL_MONEY)

    def test_manual_moov_allowed(self):
        self.assert_manual_allowed(CommissionSettlement.PaymentMode.MOOV_MONEY)

    def test_manual_bank_allowed(self):
        self.assert_manual_allowed(CommissionSettlement.PaymentMode.BANK_TRANSFER)

    def test_manual_without_confirmer_rejected(self):
        for mode in ("cash", "airtel_money", "moov_money", "bank_transfer"):
            with self.subTest(mode=mode):
                self.assert_db_rejects(self.settlement, payment_mode=mode, confirmed_by=None)

    def test_manual_with_wallet_transaction_rejected(self):
        for mode in ("cash", "airtel_money", "moov_money", "bank_transfer"):
            with self.subTest(mode=mode):
                self.assert_db_rejects(self.settlement, payment_mode=mode, wallet_transaction=self.wallet_transaction)

    def test_wallet_settlement_allowed(self):
        result = self.wallet_settlement()
        result.refresh_from_db()
        self.assertIsNone(result.confirmed_by_id)
        self.assertEqual(result.wallet_transaction_id, self.wallet_transaction.pk)
        self.assertEqual(self.wallet_transaction.commission_settlement.pk, result.pk)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("1000"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0"))
        self.assertFalse(Commission.objects.exists())

    def test_wallet_without_transaction_rejected(self):
        self.assert_db_rejects(self.wallet_settlement, wallet_transaction=None)

    def test_wallet_with_confirmer_rejected(self):
        self.assert_db_rejects(self.wallet_settlement, confirmed_by=self.admin)

    def test_wallet_transaction_cannot_fund_two_settlements(self):
        self.wallet_settlement()
        self.assert_db_rejects(self.wallet_settlement)
        self.assertEqual(CommissionSettlement.objects.count(), 1)

    def test_referenced_wallet_transaction_protected(self):
        self.wallet_settlement()
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.wallet_transaction.delete()
        self.assertTrue(WalletTransaction.objects.filter(pk=self.wallet_transaction.pk).exists())

    def test_pending_commission_without_settlement_allowed(self):
        result = self.commission()
        self.assertEqual(result.status, Commission.Status.PENDING)
        self.assertIsNone(result.settlement_id)

    def test_paid_commission_with_manual_settlement_allowed(self):
        settlement = self.settlement()
        result = self.commission(status=Commission.Status.PAID, settlement=settlement)
        self.assertEqual(result.settlement_id, settlement.pk)

    def test_paid_commission_with_wallet_settlement_allowed(self):
        settlement = self.wallet_settlement()
        result = self.commission(status=Commission.Status.PAID, settlement=settlement)
        self.assertEqual(result.settlement_id, settlement.pk)

    def test_paid_commission_without_settlement_rejected(self):
        self.assert_db_rejects(self.commission, status=Commission.Status.PAID)

    def test_legacy_confirm_settlement_still_works(self):
        commission = self.commission()
        result = confirm_commission_settlement(
            driver=self.driver, commission_ids=[commission.pk],
            payment_mode=CommissionSettlement.PaymentMode.CASH,
            reference="legacy-transition", paid_at=None, confirmed_by=self.admin,
        )
        commission.refresh_from_db()
        self.assertEqual(commission.status, Commission.Status.PAID)
        self.assertEqual(commission.settlement_id, result.pk)
        self.assertEqual(result.total_amount, Decimal("150.00"))
        self.assertEqual(result.confirmed_by_id, self.admin.pk)
        self.assertIsNone(result.wallet_transaction_id)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("1000"))
        self.assertEqual(WalletTransaction.objects.count(), 1)
