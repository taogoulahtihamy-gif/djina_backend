"""Phase 3C-2 : annulation atomique et libération de réservation.

SQLite valide ici le comportement déterministe et les rollbacks.
La concurrence réelle devra être testée sur PostgreSQL.
"""

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
    WalletTransaction,
)
from core.services.course_financial_service import (
    CourseCancellationPermissionError,
    CourseCancellationReservationError,
    CourseCancellationStateError,
    cancel_course_with_reservation_release,
)


class CourseWalletCancelTests(APITestCase):

    def setUp(self):
        self.customer_user = CustomUser.objects.create_user(
            email="cancel-customer@example.com",
            phone="+23567771001",
            user_type="customer",
        )
        self.customer = Customer.objects.create(user=self.customer_user)

        self.other_customer_user = CustomUser.objects.create_user(
            email="cancel-other@example.com",
            phone="+23567771002",
            user_type="customer",
        )
        Customer.objects.create(user=self.other_customer_user)

        self.driver_user = CustomUser.objects.create_user(
            email="cancel-driver@example.com",
            phone="+23567771003",
            user_type="driver",
        )
        self.driver = Driver.objects.create(
            user=self.driver_user,
            is_enabled=True,
        )

        self.other_driver_user = CustomUser.objects.create_user(
            email="cancel-driver2@example.com",
            phone="+23567771004",
            user_type="driver",
        )
        self.other_driver = Driver.objects.create(
            user=self.other_driver_user,
            is_enabled=True,
        )

        self.admin_user = CustomUser.objects.create_user(
            email="cancel-admin@example.com",
            phone="+23567771005",
            user_type="admin",
            is_staff=True,
        )

        self.wallet = DriverWallet.objects.create(
            driver=self.driver,
            balance=Decimal("1000.00"),
            reserved_balance=Decimal("150.00"),
        )

        self.course = self.make_course(
            status=Course.Status.ACCEPTED,
            driver=self.driver,
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

    def make_course(self, *, status, driver=None):
        return Course.objects.create(
            customer=self.customer,
            driver=driver,
            status=status,
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

    def refresh_all(self):
        self.course.refresh_from_db()
        self.wallet.refresh_from_db()
        self.reservation.refresh_from_db()

    def test_customer_cancel_releases_reservation(self):
        course, cancelled_by = cancel_course_with_reservation_release(
            course_id=self.course.pk,
            user=self.customer_user,
            reason="Changed plans",
        )

        self.refresh_all()

        self.assertEqual(course.pk, self.course.pk)
        self.assertEqual(cancelled_by, Course.CancelledBy.CUSTOMER)

        self.assertEqual(self.course.status, Course.Status.CANCELLED)
        self.assertEqual(
            self.course.cancelled_by,
            Course.CancelledBy.CUSTOMER,
        )
        self.assertEqual(
            self.course.cancellation_reason,
            "Changed plans",
        )
        self.assertIsNotNone(self.course.cancelled_at)

        self.assertEqual(
            self.reservation.status,
            CommissionReservation.Status.RELEASED,
        )
        self.assertIsNotNone(self.reservation.released_at)
        self.assertIsNone(self.reservation.consumed_at)

        self.assertEqual(self.wallet.balance, Decimal("1000.00"))
        self.assertEqual(
            self.wallet.reserved_balance,
            Decimal("0.00"),
        )

    def test_driver_can_cancel_own_course(self):
        course, cancelled_by = cancel_course_with_reservation_release(
            course_id=self.course.pk,
            user=self.driver_user,
        )

        self.assertEqual(course.status, Course.Status.CANCELLED)
        self.assertEqual(cancelled_by, Course.CancelledBy.DRIVER)

    def test_admin_can_cancel(self):
        course, cancelled_by = cancel_course_with_reservation_release(
            course_id=self.course.pk,
            user=self.admin_user,
        )

        self.assertEqual(course.status, Course.Status.CANCELLED)
        self.assertEqual(cancelled_by, Course.CancelledBy.ADMIN)

    def test_other_customer_forbidden(self):
        with self.assertRaises(CourseCancellationPermissionError):
            cancel_course_with_reservation_release(
                course_id=self.course.pk,
                user=self.other_customer_user,
            )

        self.refresh_all()

        self.assertEqual(self.course.status, Course.Status.ACCEPTED)
        self.assertEqual(
            self.reservation.status,
            CommissionReservation.Status.ACTIVE,
        )
        self.assertEqual(
            self.wallet.reserved_balance,
            Decimal("150.00"),
        )

    def test_other_driver_forbidden(self):
        with self.assertRaises(CourseCancellationPermissionError):
            cancel_course_with_reservation_release(
                course_id=self.course.pk,
                user=self.other_driver_user,
            )

    def test_completed_course_cannot_be_cancelled(self):
        Course.objects.filter(pk=self.course.pk).update(
            status=Course.Status.COMPLETED
        )

        with self.assertRaises(CourseCancellationStateError):
            cancel_course_with_reservation_release(
                course_id=self.course.pk,
                user=self.customer_user,
            )

    def test_cancelled_course_cannot_be_cancelled_again(self):
        Course.objects.filter(pk=self.course.pk).update(
            status=Course.Status.CANCELLED
        )

        with self.assertRaises(CourseCancellationStateError):
            cancel_course_with_reservation_release(
                course_id=self.course.pk,
                user=self.customer_user,
            )

    def test_arriving_course_can_be_cancelled(self):
        Course.objects.filter(pk=self.course.pk).update(
            status=Course.Status.ARRIVING
        )

        cancel_course_with_reservation_release(
            course_id=self.course.pk,
            user=self.customer_user,
        )

        self.refresh_all()

        self.assertEqual(self.course.status, Course.Status.CANCELLED)
        self.assertEqual(
            self.reservation.status,
            CommissionReservation.Status.RELEASED,
        )

    def test_picked_up_course_keeps_legacy_cancel_contract(self):
        Course.objects.filter(pk=self.course.pk).update(
            status=Course.Status.PICKED_UP
        )

        cancel_course_with_reservation_release(
            course_id=self.course.pk,
            user=self.customer_user,
        )

        self.course.refresh_from_db()
        self.assertEqual(self.course.status, Course.Status.CANCELLED)

    def test_legacy_course_without_reservation_can_cancel(self):
        self.reservation.delete()

        DriverWallet.objects.filter(pk=self.wallet.pk).update(
            reserved_balance=Decimal("0.00")
        )

        legacy = self.make_course(
            status=Course.Status.ACCEPTED,
            driver=self.driver,
        )

        course, _ = cancel_course_with_reservation_release(
            course_id=legacy.pk,
            user=self.customer_user,
        )

        self.assertEqual(course.status, Course.Status.CANCELLED)

    def test_requested_course_without_reservation_can_cancel(self):
        self.reservation.delete()

        DriverWallet.objects.filter(pk=self.wallet.pk).update(
            reserved_balance=Decimal("0.00")
        )

        requested = self.make_course(
            status=Course.Status.REQUESTED,
            driver=None,
        )

        course, _ = cancel_course_with_reservation_release(
            course_id=requested.pk,
            user=self.customer_user,
        )

        self.assertEqual(course.status, Course.Status.CANCELLED)

    def test_released_reservation_allows_cancel(self):
        CommissionReservation.objects.filter(
            pk=self.reservation.pk
        ).update(
            status=CommissionReservation.Status.RELEASED,
            released_at=self.course.created_at,
        )

        DriverWallet.objects.filter(pk=self.wallet.pk).update(
            reserved_balance=Decimal("0.00")
        )

        course, _ = cancel_course_with_reservation_release(
            course_id=self.course.pk,
            user=self.customer_user,
        )

        self.assertEqual(course.status, Course.Status.CANCELLED)

    def test_consumed_reservation_blocks_cancel(self):
        CommissionReservation.objects.filter(
            pk=self.reservation.pk
        ).update(
            status=CommissionReservation.Status.CONSUMED,
            consumed_at=self.course.created_at,
        )

        DriverWallet.objects.filter(pk=self.wallet.pk).update(
            reserved_balance=Decimal("0.00")
        )

        with self.assertRaises(CourseCancellationReservationError):
            cancel_course_with_reservation_release(
                course_id=self.course.pk,
                user=self.customer_user,
            )

        self.course.refresh_from_db()

        self.assertEqual(self.course.status, Course.Status.ACCEPTED)

    def test_inconsistent_reserve_blocks_cancel(self):
        DriverWallet.objects.filter(pk=self.wallet.pk).update(
            reserved_balance=Decimal("149.00")
        )

        with self.assertRaises(CourseCancellationReservationError):
            cancel_course_with_reservation_release(
                course_id=self.course.pk,
                user=self.customer_user,
            )

        self.course.refresh_from_db()
        self.reservation.refresh_from_db()

        self.assertEqual(self.course.status, Course.Status.ACCEPTED)
        self.assertEqual(
            self.reservation.status,
            CommissionReservation.Status.ACTIVE,
        )

    def test_blocked_wallet_can_release(self):
        DriverWallet.objects.filter(pk=self.wallet.pk).update(
            status=DriverWallet.Status.BLOCKED
        )

        cancel_course_with_reservation_release(
            course_id=self.course.pk,
            user=self.customer_user,
        )

        self.refresh_all()

        self.assertEqual(
            self.wallet.reserved_balance,
            Decimal("0.00"),
        )
        self.assertEqual(
            self.reservation.status,
            CommissionReservation.Status.RELEASED,
        )

    def test_closed_wallet_can_release(self):
        DriverWallet.objects.filter(pk=self.wallet.pk).update(
            status=DriverWallet.Status.CLOSED
        )

        cancel_course_with_reservation_release(
            course_id=self.course.pk,
            user=self.customer_user,
        )

        self.refresh_all()

        self.assertEqual(
            self.wallet.reserved_balance,
            Decimal("0.00"),
        )

    def test_course_save_failure_rolls_back_release(self):
        real_save = Course.save

        def fail_cancel(instance, *args, **kwargs):
            if instance.status == Course.Status.CANCELLED:
                raise IntegrityError("forced cancel failure")

            return real_save(instance, *args, **kwargs)

        with patch.object(
            Course,
            "save",
            autospec=True,
            side_effect=fail_cancel,
        ):
            with self.assertRaises(IntegrityError):
                cancel_course_with_reservation_release(
                    course_id=self.course.pk,
                    user=self.customer_user,
                )

        self.refresh_all()

        self.assertEqual(self.course.status, Course.Status.ACCEPTED)
        self.assertEqual(
            self.reservation.status,
            CommissionReservation.Status.ACTIVE,
        )
        self.assertEqual(
            self.wallet.reserved_balance,
            Decimal("150.00"),
        )
        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

    def test_no_financial_ledger_created(self):
        cancel_course_with_reservation_release(
            course_id=self.course.pk,
            user=self.customer_user,
        )

        self.assertFalse(WalletTransaction.objects.exists())
        self.assertFalse(Commission.objects.exists())
        self.assertFalse(CommissionSettlement.objects.exists())

    def test_endpoint_customer_cancel(self):
        self.client.force_authenticate(self.customer_user)

        response = self.client.post(
            reverse("courses-cancel", args=[self.course.pk]),
            {"reason": "No longer needed"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        self.refresh_all()

        self.assertEqual(self.course.status, Course.Status.CANCELLED)
        self.assertEqual(
            self.reservation.status,
            CommissionReservation.Status.RELEASED,
        )

    def test_endpoint_other_customer_403(self):
        self.client.force_authenticate(self.other_customer_user)

        response = self.client.post(
            reverse("courses-cancel", args=[self.course.pk]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_endpoint_consumed_reservation_409(self):
        CommissionReservation.objects.filter(
            pk=self.reservation.pk
        ).update(
            status=CommissionReservation.Status.CONSUMED,
            consumed_at=self.course.created_at,
        )

        DriverWallet.objects.filter(pk=self.wallet.pk).update(
            reserved_balance=Decimal("0.00")
        )

        self.client.force_authenticate(self.customer_user)

        response = self.client.post(
            reverse("courses-cancel", args=[self.course.pk]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 409)

    def test_endpoint_completed_course_400(self):
        Course.objects.filter(pk=self.course.pk).update(
            status=Course.Status.COMPLETED
        )

        self.client.force_authenticate(self.customer_user)

        response = self.client.post(
            reverse("courses-cancel", args=[self.course.pk]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
