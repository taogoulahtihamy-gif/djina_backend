"""Règlement atomique d'une commission déjà réservée."""

from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase

from core.models import (
    Commission,
    CommissionReservation,
    Course,
    Customer,
    CustomUser,
    Driver,
    DriverWallet,
    WalletTransaction,
)
from core.services.wallet_service import (
    CommissionReservationStateError,
    WalletIdempotencyConflictError,
    WalletTransactionConsistencyError,
    settle_commission_reservation,
)


class WalletCommissionSettlementTests(TestCase):

    def setUp(self):
        customer_user = CustomUser.objects.create_user(
            email="settle-customer@example.com",
            phone="+23567772001",
            user_type="customer",
        )

        customer = Customer.objects.create(
            user=customer_user
        )

        driver_user = CustomUser.objects.create_user(
            email="settle-driver@example.com",
            phone="+23567772002",
            user_type="driver",
        )

        self.driver = Driver.objects.create(
            user=driver_user,
            is_enabled=True,
        )

        self.course = Course.objects.create(
            customer=customer,
            driver=self.driver,
            status=Course.Status.PICKED_UP,
            departure_latitude=Decimal("12.100000"),
            departure_longitude=Decimal("15.100000"),
            destination_latitude=Decimal("12.200000"),
            destination_longitude=Decimal("15.200000"),
            starting_landmark="Chagoua",
            arrival_landmark="Farcha",
            distance_km=Decimal("5.000"),
            initial_price=Decimal("1000.00"),
            final_price=Decimal("0.00"),
        )

        self.wallet = DriverWallet.objects.create(
            driver=self.driver,
            balance=Decimal("1000.00"),
            reserved_balance=Decimal("150.00"),
        )

        self.reservation = CommissionReservation.objects.create(
            wallet=self.wallet,
            driver=self.driver,
            course=self.course,
            gross_amount=Decimal("1000.00"),
            commission_rate=Decimal("15.00"),
            estimated_amount=Decimal("150.00"),
            status=CommissionReservation.Status.ACTIVE,
        )

        self.commission = Commission.objects.create(
            course=self.course,
            driver=self.driver,
            gross_amount=Decimal("1000.00"),
            commission_rate=Decimal("15.00"),
            commission_amount=Decimal("150.00"),
            driver_net_amount=Decimal("850.00"),
            status=Commission.Status.PENDING,
        )

    def settle(self, **kwargs):
        values = {
            "reservation": self.reservation,
            "commission": self.commission,
            "idempotency_key": f"course:{self.course.pk}:commission",
        }
        values.update(kwargs)

        return settle_commission_reservation(**values)

    def refresh(self):
        self.wallet.refresh_from_db()
        self.reservation.refresh_from_db()

    def test_settle_debits_reserved_commission(self):
        transaction_record = self.settle()

        self.refresh()

        self.assertEqual(
            self.wallet.balance,
            Decimal("850.00"),
        )
        self.assertEqual(
            self.wallet.reserved_balance,
            Decimal("0.00"),
        )
        self.assertEqual(
            self.reservation.status,
            CommissionReservation.Status.CONSUMED,
        )
        self.assertIsNotNone(
            self.reservation.consumed_at
        )

        self.assertEqual(
            transaction_record.amount,
            Decimal("150.00"),
        )
        self.assertEqual(
            transaction_record.type,
            WalletTransaction.Type.COMMISSION,
        )
        self.assertEqual(
            transaction_record.direction,
            WalletTransaction.Direction.DEBIT,
        )
        self.assertEqual(
            transaction_record.status,
            WalletTransaction.Status.SUCCESS,
        )
        self.assertEqual(
            transaction_record.course_id,
            self.course.pk,
        )
        self.assertEqual(
            transaction_record.commission_id,
            self.commission.pk,
        )

    def test_available_balance_does_not_change(self):
        available_before = self.wallet.available_balance

        self.settle()

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.available_balance,
            available_before,
        )

    def test_blocked_wallet_can_settle_reserved_commission(self):
        DriverWallet.objects.filter(
            pk=self.wallet.pk
        ).update(
            status=DriverWallet.Status.BLOCKED
        )

        self.settle()

        self.refresh()

        self.assertEqual(
            self.wallet.balance,
            Decimal("850.00"),
        )
        self.assertEqual(
            self.wallet.reserved_balance,
            Decimal("0.00"),
        )

    def test_closed_wallet_can_settle_reserved_commission(self):
        DriverWallet.objects.filter(
            pk=self.wallet.pk
        ).update(
            status=DriverWallet.Status.CLOSED
        )

        self.settle()

        self.refresh()

        self.assertEqual(
            self.wallet.balance,
            Decimal("850.00"),
        )

    def test_released_reservation_cannot_settle(self):
        CommissionReservation.objects.filter(
            pk=self.reservation.pk
        ).update(
            status=CommissionReservation.Status.RELEASED,
            released_at=self.course.created_at,
        )

        DriverWallet.objects.filter(
            pk=self.wallet.pk
        ).update(
            reserved_balance=Decimal("0.00")
        )

        with self.assertRaises(
            CommissionReservationStateError
        ):
            self.settle()

    def test_insufficient_reserved_balance_rolls_back(self):
        DriverWallet.objects.filter(
            pk=self.wallet.pk
        ).update(
            reserved_balance=Decimal("149.00")
        )

        with self.assertRaises(
            WalletTransactionConsistencyError
        ):
            self.settle()

        self.wallet.refresh_from_db()
        self.reservation.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )
        self.assertEqual(
            self.wallet.reserved_balance,
            Decimal("149.00"),
        )
        self.assertEqual(
            self.reservation.status,
            CommissionReservation.Status.ACTIVE,
        )
        self.assertFalse(
            WalletTransaction.objects.exists()
        )

    def test_retry_after_success_is_idempotent(self):
        first = self.settle()

        second = self.settle()

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            WalletTransaction.objects.count(),
            1,
        )

        self.refresh()

        self.assertEqual(
            self.wallet.balance,
            Decimal("850.00"),
        )

    def test_consumed_without_transaction_is_inconsistent(self):
        CommissionReservation.objects.filter(
            pk=self.reservation.pk
        ).update(
            status=CommissionReservation.Status.CONSUMED,
            consumed_at=self.course.created_at,
        )

        DriverWallet.objects.filter(
            pk=self.wallet.pk
        ).update(
            reserved_balance=Decimal("0.00"),
            balance=Decimal("850.00"),
        )

        with self.assertRaises(
            WalletTransactionConsistencyError
        ):
            self.settle()

    def test_wrong_idempotency_key_collision_rejected(self):
        WalletTransaction.objects.create(
            wallet=self.wallet,
            type=WalletTransaction.Type.ADJUSTMENT,
            direction=WalletTransaction.Direction.CREDIT,
            amount=Decimal("1.00"),
            balance_before=Decimal("1000.00"),
            balance_after=Decimal("1001.00"),
            idempotency_key=f"course:{self.course.pk}:commission",
            status=WalletTransaction.Status.SUCCESS,
        )

        with self.assertRaises(
            WalletIdempotencyConflictError
        ):
            self.settle()

        self.refresh()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )
        self.assertEqual(
            self.reservation.status,
            CommissionReservation.Status.ACTIVE,
        )

    def test_wallet_transaction_failure_rolls_back_everything(self):
        with patch(
            "core.services.wallet_service.WalletTransaction.objects.create",
            side_effect=IntegrityError("forced ledger failure"),
        ):
            with self.assertRaises(IntegrityError):
                self.settle()

        self.refresh()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )
        self.assertEqual(
            self.wallet.reserved_balance,
            Decimal("150.00"),
        )
        self.assertEqual(
            self.reservation.status,
            CommissionReservation.Status.ACTIVE,
        )
        self.assertFalse(
            WalletTransaction.objects.exists()
        )
