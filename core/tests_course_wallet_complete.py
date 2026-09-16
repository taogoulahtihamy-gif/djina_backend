"""Phase 3C-3B : completion atomique Course -> Wallet -> Commission."""

from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import (
    Commission,
    CommissionReservation,
    CommissionSettlement,
    Course,
    Customer,
    CustomUser,
    Driver,
    DriverWallet,
    Payment,
    WalletTransaction,
)
from core.services.course_financial_service import (
    CourseCompletionFinancialError,
    CourseCompletionPermissionError,
    CourseCompletionStateError,
    complete_course_with_wallet_commission,
)


class CourseWalletCompleteTests(APITestCase):

    def setUp(self):
        customer_user = CustomUser.objects.create_user(
            email="complete-customer@example.com",
            phone="+23567773001",
            user_type="customer",
        )
        customer = Customer.objects.create(user=customer_user)

        self.driver_user = CustomUser.objects.create_user(
            email="complete-driver@example.com",
            phone="+23567773002",
            user_type="driver",
        )
        self.driver = Driver.objects.create(
            user=self.driver_user,
            is_enabled=True,
        )

        self.other_driver_user = CustomUser.objects.create_user(
            email="complete-other@example.com",
            phone="+23567773003",
            user_type="driver",
        )
        self.other_driver = Driver.objects.create(
            user=self.other_driver_user,
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

    def complete(self):
        return complete_course_with_wallet_commission(
            course_id=self.course.pk,
            user=self.driver_user,
        )

    def refresh(self):
        self.course.refresh_from_db()
        self.wallet.refresh_from_db()
        self.reservation.refresh_from_db()

    def test_complete_full_financial_flow(self):
        result = self.complete()
        self.refresh()

        self.assertEqual(result.pk, self.course.pk)
        self.assertEqual(
            self.course.status,
            Course.Status.COMPLETED,
        )
        self.assertEqual(
            self.course.final_price,
            Decimal("1000.00"),
        )
        self.assertIsNotNone(self.course.completed_at)

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

        tx = WalletTransaction.objects.get()
        self.assertEqual(tx.amount, Decimal("150.00"))
        self.assertEqual(
            tx.type,
            WalletTransaction.Type.COMMISSION,
        )

        commission = Commission.objects.get(
            course=self.course
        )
        self.assertEqual(
            commission.status,
            Commission.Status.PAID,
        )
        self.assertEqual(
            commission.gross_amount,
            Decimal("1000.00"),
        )
        self.assertEqual(
            commission.commission_rate,
            Decimal("15.00"),
        )
        self.assertEqual(
            commission.commission_amount,
            Decimal("150.00"),
        )
        self.assertEqual(
            commission.driver_net_amount,
            Decimal("850.00"),
        )

        settlement = CommissionSettlement.objects.get(
            pk=commission.settlement_id
        )
        self.assertEqual(
            settlement.payment_mode,
            CommissionSettlement.PaymentMode.WALLET,
        )
        self.assertIsNone(settlement.confirmed_by_id)
        self.assertEqual(
            settlement.wallet_transaction_id,
            tx.pk,
        )

    def test_fake_final_price_from_api_is_ignored(self):
        self.client.force_authenticate(self.driver_user)

        response = self.client.post(
            reverse("courses-complete", args=[self.course.pk]),
            {"final_price": "1.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        self.course.refresh_from_db()

        self.assertEqual(
            self.course.final_price,
            Decimal("1000.00"),
        )

        self.assertEqual(
            Commission.objects.get(
                course=self.course
            ).commission_amount,
            Decimal("150.00"),
        )

    def test_complete_without_final_price_payload(self):
        self.client.force_authenticate(self.driver_user)

        response = self.client.post(
            reverse("courses-complete", args=[self.course.pk]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

    def test_changed_initial_price_does_not_change_snapshot(self):
        Course.objects.filter(pk=self.course.pk).update(
            initial_price=Decimal("5000.00")
        )

        self.complete()

        self.course.refresh_from_db()
        commission = Commission.objects.get(course=self.course)

        self.assertEqual(
            self.course.final_price,
            Decimal("1000.00"),
        )
        self.assertEqual(
            commission.commission_amount,
            Decimal("150.00"),
        )

    def test_blocked_wallet_can_finish_reserved_course(self):
        DriverWallet.objects.filter(pk=self.wallet.pk).update(
            status=DriverWallet.Status.BLOCKED
        )

        self.complete()
        self.refresh()

        self.assertEqual(
            self.course.status,
            Course.Status.COMPLETED,
        )
        self.assertEqual(
            self.wallet.balance,
            Decimal("850.00"),
        )

    def test_closed_wallet_can_finish_reserved_course(self):
        DriverWallet.objects.filter(pk=self.wallet.pk).update(
            status=DriverWallet.Status.CLOSED
        )

        self.complete()
        self.refresh()

        self.assertEqual(
            self.course.status,
            Course.Status.COMPLETED,
        )

    def test_wrong_driver_rejected(self):
        with self.assertRaises(
            CourseCompletionPermissionError
        ):
            complete_course_with_wallet_commission(
                course_id=self.course.pk,
                user=self.other_driver_user,
            )

        self.refresh()

        self.assertEqual(
            self.course.status,
            Course.Status.PICKED_UP,
        )
        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

    def test_wrong_state_rejected(self):
        Course.objects.filter(pk=self.course.pk).update(
            status=Course.Status.ACCEPTED
        )

        with self.assertRaises(
            CourseCompletionStateError
        ):
            self.complete()

    def test_missing_reservation_rejected(self):
        self.reservation.delete()

        DriverWallet.objects.filter(pk=self.wallet.pk).update(
            reserved_balance=Decimal("0.00")
        )

        with self.assertRaises(
            CourseCompletionFinancialError
        ):
            self.complete()

    def test_released_reservation_rejected(self):
        CommissionReservation.objects.filter(
            pk=self.reservation.pk
        ).update(
            status=CommissionReservation.Status.RELEASED,
            released_at=self.course.created_at,
        )

        DriverWallet.objects.filter(pk=self.wallet.pk).update(
            reserved_balance=Decimal("0.00")
        )

        with self.assertRaises(
            CourseCompletionFinancialError
        ):
            self.complete()

    def test_inconsistent_snapshot_rejected(self):
        CommissionReservation.objects.filter(
            pk=self.reservation.pk
        ).update(
            estimated_amount=Decimal("149.00")
        )

        with self.assertRaises(
            CourseCompletionFinancialError
        ):
            self.complete()

        self.assertFalse(Commission.objects.exists())
        self.assertFalse(WalletTransaction.objects.exists())

    def test_settlement_failure_rolls_back_everything(self):
        with patch(
            "core.models.CommissionSettlement.objects.create",
            side_effect=IntegrityError("forced settlement failure"),
        ):
            with self.assertRaises(IntegrityError):
                self.complete()

        self.refresh()

        self.assertEqual(
            self.course.status,
            Course.Status.PICKED_UP,
        )
        self.assertEqual(
            self.course.final_price,
            Decimal("0.00"),
        )
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

        self.assertFalse(Commission.objects.exists())
        self.assertFalse(WalletTransaction.objects.exists())
        self.assertFalse(CommissionSettlement.objects.exists())

    def test_payment_is_not_created_or_modified(self):
        self.complete()
        self.assertFalse(Payment.objects.exists())

    def test_signal_does_not_duplicate_commission(self):
        self.complete()

        self.assertEqual(
            Commission.objects.filter(
                course=self.course
            ).count(),
            1,
        )

    def test_second_complete_does_not_double_debit(self):
        self.client.force_authenticate(self.driver_user)

        first = self.client.post(
            reverse("courses-complete", args=[self.course.pk]),
            {},
            format="json",
        )

        second = self.client.post(
            reverse("courses-complete", args=[self.course.pk]),
            {},
            format="json",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("850.00"),
        )
        self.assertEqual(
            WalletTransaction.objects.count(),
            1,
        )
        self.assertEqual(
            Commission.objects.count(),
            1,
        )
        self.assertEqual(
            CommissionSettlement.objects.count(),
            1,
        )
