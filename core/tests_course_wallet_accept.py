"""Service et route réelle d'acceptation financière."""
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError, connection
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import (
    Commission, CommissionReservation, CommissionSettlement, CommissionSetting,
    Course, Customer, CustomUser, Driver, DriverWallet, Payment, Vehicle, WalletTransaction,
)
from core.services.course_financial_service import (
    CourseAcceptanceStateError, accept_course_with_commission_reservation,
)
from core.services.wallet_service import reserve_commission, release_commission_reservation, consume_commission_reservation


class CourseWalletAcceptTests(APITestCase):
    def setUp(self):
        self.driver = self.make_driver("1")
        self.other_driver = self.make_driver("2")
        user = CustomUser.objects.create_user(email="customer@example.com", phone="3", user_type="customer")
        self.customer = Customer.objects.create(user=user)
        self.course = Course.objects.create(
            customer=self.customer, departure_latitude="12.1", departure_longitude="15.1",
            destination_latitude="12.2", destination_longitude="15.2",
            starting_landmark="A", arrival_landmark="B", initial_price="2000.00", final_price="123.00",
        )
        self.wallet = DriverWallet.objects.create(driver=self.driver, balance="10000.00")
        self.setting = CommissionSetting.objects.create(rate="15.00")
        self.client.force_authenticate(self.driver.user)

    def make_driver(self, suffix):
        user = CustomUser.objects.create_user(email=f"driver{suffix}@example.com", phone=suffix, user_type="driver")
        return Driver.objects.create(user=user)

    def accept(self, payload=None):
        return self.client.post(reverse("courses-accept", args=[self.course.pk]), payload or {}, format="json")

    def service(self, **kwargs):
        return accept_course_with_commission_reservation(course_id=self.course.pk, driver=self.driver, **kwargs)

    def reservation(self, snapshot=False):
        result = reserve_commission(wallet=self.wallet, driver=self.driver, course=self.course, estimated_amount=Decimal("300"))
        if snapshot:
            result.gross_amount = Decimal("2000")
            result.commission_rate = Decimal("15")
            result.save()
        return result

    def assert_no_financial_records(self):
        self.assertFalse(WalletTransaction.objects.exists())
        self.assertFalse(Commission.objects.exists())
        self.assertFalse(CommissionSettlement.objects.exists())

    def assert_failed(self, code, payload=None, reserved="0", reservations=0):
        self.course.refresh_from_db()
        before = (self.course.status, self.course.driver_id, self.course.accepted_at, self.course.final_price)
        self.assertEqual(self.accept(payload).status_code, code)
        self.course.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual((self.course.status, self.course.driver_id, self.course.accepted_at, self.course.final_price), before)
        self.assertEqual(self.wallet.balance, Decimal("10000"))
        self.assertEqual(self.wallet.reserved_balance, Decimal(reserved))
        self.assertEqual(CommissionReservation.objects.count(), reservations)
        self.assert_no_financial_records()

    def test_api_happy_path_and_financial_invariants(self):
        self.assertEqual(self.accept().status_code, 200)
        self.course.refresh_from_db()
        self.wallet.refresh_from_db()
        r = CommissionReservation.objects.get(course=self.course)
        self.assertEqual(self.course.status, Course.Status.ACCEPTED)
        self.assertEqual(self.course.driver_id, self.driver.pk)
        self.assertIsNotNone(self.course.accepted_at)
        self.assertIsNone(self.course.vehicle_id)
        self.assertEqual((r.gross_amount, r.commission_rate, r.estimated_amount), (Decimal("2000"), Decimal("15"), Decimal("300")))
        self.assertEqual(self.wallet.balance, Decimal("10000"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("300"))
        self.assertEqual(self.wallet.available_balance, Decimal("9700"))
        self.assertEqual(self.course.final_price, Decimal("123"))
        self.assert_no_financial_records()

    def test_round_half_up(self):
        self.course.initial_price = Decimal("2000.10")
        self.course.save(update_fields=["initial_price"])
        self.service()
        self.assertEqual(CommissionReservation.objects.get(course=self.course).estimated_amount, Decimal("300.02"))

    def test_foreign_legacy_reservation_rejected(self):
        wallet = DriverWallet.objects.create(driver=self.other_driver, balance="10000")
        r = reserve_commission(wallet=wallet, driver=self.other_driver, course=self.course, estimated_amount=Decimal("300"))
        self.assert_failed(409, reservations=1)
        r.refresh_from_db()
        wallet.refresh_from_db()
        self.assertIsNone(r.gross_amount)
        self.assertIsNone(r.commission_rate)
        self.assertEqual(wallet.reserved_balance, Decimal("300"))

    def test_service_happy_path(self):
        self.assertEqual(self.service().status, Course.Status.ACCEPTED)

    def test_rate_changed_after_accept_preserves_snapshot(self):
        self.service()
        self.setting.rate = Decimal("20")
        self.setting.save()
        self.assertEqual(CommissionReservation.objects.get(course=self.course).commission_rate, Decimal("15"))

    def test_price_changed_after_accept_preserves_snapshot(self):
        self.service()
        self.course.initial_price = Decimal("4000")
        self.course.save(update_fields=["initial_price"])
        self.assertEqual(CommissionReservation.objects.get(course=self.course).gross_amount, Decimal("2000"))

    def test_forged_final_price_ignored(self):
        self.assertEqual(self.accept({"final_price": "1"}).status_code, 200)
        self.assertEqual(CommissionReservation.objects.get(course=self.course).estimated_amount, Decimal("300"))

    def test_forged_rate_ignored(self):
        self.assertEqual(self.accept({"commission_rate": "0"}).status_code, 200)
        self.assertEqual(CommissionReservation.objects.get(course=self.course).commission_rate, Decimal("15"))

    def test_insufficient_balance(self):
        self.wallet.reserved_balance = Decimal("9800")
        self.wallet.save()
        self.assert_failed(409, reserved="9800")

    def test_missing_wallet_creation_rolls_back(self):
        self.wallet.delete()
        self.assertEqual(self.accept().status_code, 409)
        self.assertFalse(DriverWallet.objects.filter(driver=self.driver).exists())
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, Course.Status.REQUESTED)
        self.assertFalse(CommissionReservation.objects.exists())

    def vehicle(self, **kwargs):
        values = dict(driver=self.driver, type="car", model="Test", license_plate="TEST")
        values.update(kwargs)
        return Vehicle.objects.create(**values)

    def test_own_vehicle(self):
        v = self.vehicle()
        self.assertEqual(self.accept({"vehicle_id": v.pk}).status_code, 200)
        self.course.refresh_from_db()
        self.assertEqual(self.course.vehicle_id, v.pk)

    def test_other_vehicle(self):
        self.assert_failed(400, {"vehicle_id": self.vehicle(driver=self.other_driver).pk})

    def test_inactive_vehicle(self):
        self.assert_failed(400, {"vehicle_id": self.vehicle(is_active=False).pk})

    def test_deleted_vehicle(self):
        self.assert_failed(400, {"vehicle_id": self.vehicle(deleted_at=timezone.now()).pk})

    def test_missing_vehicle(self):
        self.assert_failed(400, {"vehicle_id": 999999})

    def test_second_driver_cannot_accept(self):
        self.service()
        self.client.force_authenticate(self.other_driver.user)
        self.assert_failed(400, reserved="300", reservations=1)
        self.assertFalse(DriverWallet.objects.filter(driver=self.other_driver).exists())

    def test_legacy_snapshot_filled(self):
        r = self.reservation()
        self.service()
        r.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual((r.gross_amount, r.commission_rate), (Decimal("2000"), Decimal("15")))
        self.assertEqual(self.wallet.reserved_balance, Decimal("300"))

    def test_identical_snapshot_no_double_reserve(self):
        self.reservation(snapshot=True)
        self.service()
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("300"))
        self.assertEqual(CommissionReservation.objects.count(), 1)

    def test_different_snapshot_rolls_back(self):
        r = self.reservation(snapshot=True)
        r.gross_amount = Decimal("3000")
        r.save()
        self.assert_failed(409, reserved="300", reservations=1)
        r.refresh_from_db()
        self.assertEqual(r.gross_amount, Decimal("3000"))

    def test_different_estimate_rolls_back(self):
        self.reservation()
        self.course.initial_price = Decimal("3000")
        self.course.save()
        self.assert_failed(409, reserved="300", reservations=1)
        self.assertIsNone(CommissionReservation.objects.get(course=self.course).gross_amount)

    def test_released_reservation_rejected(self):
        r = self.reservation()
        release_commission_reservation(reservation=r)
        self.assert_failed(409, reservations=1)
        r.refresh_from_db()
        self.assertEqual(r.status, CommissionReservation.Status.RELEASED)
        self.assertIsNone(r.gross_amount)

    def test_consumed_reservation_rejected(self):
        r = self.reservation()
        consume_commission_reservation(reservation=r)
        self.assert_failed(409, reservations=1)
        r.refresh_from_db()
        self.assertEqual(r.status, CommissionReservation.Status.CONSUMED)

    def test_save_course_failure_rolls_back(self):
        original_save = Course.save
        def fail_accept(instance, *args, **kwargs):
            if instance.status == Course.Status.ACCEPTED:
                raise IntegrityError("deterministic failure")
            return original_save(instance, *args, **kwargs)
        with patch.object(Course, "save", fail_accept):
            with self.assertRaises(IntegrityError):
                self.service()
        self.course.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(self.course.status, Course.Status.REQUESTED)
        self.assertIsNone(self.course.driver_id)
        self.assertIsNone(self.course.accepted_at)
        self.assertEqual(self.wallet.reserved_balance, Decimal("0"))
        self.assertEqual(self.wallet.balance, Decimal("10000"))
        self.assertFalse(CommissionReservation.objects.exists())
        self.assert_no_financial_records()

    def test_deterministic_lock_order(self):
        # SQLite tests deterministic lock ordering only.
        # PostgreSQL concurrent integration testing is still required.
        # QuerySet emits SELECT FOR UPDATE on PostgreSQL; SQLite omits the
        # clause. Inspect the real queryset flag without mocking operations.
        from django.db.models import QuerySet
        events = []
        def execute(execute, sql, params, many, context):
            import inspect
            frame = inspect.currentframe()
            while frame:
                queryset = frame.f_locals.get("self")
                if isinstance(queryset, QuerySet) and queryset.model in (Course, DriverWallet, CommissionReservation):
                    if queryset.query.select_for_update:
                        events.append(queryset.model)
                    break
                frame = frame.f_back
            return execute(sql, params, many, context)
        with connection.execute_wrapper(execute):
            self.service()
        self.assertEqual(events[0], Course)
        self.assertLess(events.index(Course), events.index(DriverWallet))
        self.assertLess(events.index(DriverWallet), events.index(CommissionReservation))

    def test_payment_unchanged(self):
        payment = Payment.objects.create(course=self.course, final_amount="123", payment_mode="cash")
        before = Payment.objects.values().get(pk=payment.pk)
        self.service()
        self.assertEqual(Payment.objects.values().get(pk=payment.pk), before)
        self.assert_no_financial_records()

    def test_missing_course(self):
        with self.assertRaises(CourseAcceptanceStateError):
            accept_course_with_commission_reservation(course_id=999999, driver=self.driver)

    def test_deleted_course(self):
        self.course.deleted_at = timezone.now()
        self.course.save()
        self.assert_failed(400)

    def test_unsaved_driver(self):
        with self.assertRaises(CourseAcceptanceStateError):
            accept_course_with_commission_reservation(course_id=self.course.pk, driver=Driver(user=self.other_driver.user))

    def test_customer_permission(self):
        self.client.force_authenticate(self.customer.user)
        self.assert_failed(403)

    def test_admin_permission(self):
        user = CustomUser.objects.create_superuser(email="admin@example.com", phone="4", user_type="admin")
        self.client.force_authenticate(user)
        self.assert_failed(403)


def state_test(state):
    def test(self):
        self.course.status = state
        self.course.save(update_fields=["status"])
        self.assert_failed(400)
    return test


for state in (Course.Status.ACCEPTED, Course.Status.ARRIVING, Course.Status.PICKED_UP, Course.Status.COMPLETED, Course.Status.CANCELLED):
    setattr(CourseWalletAcceptTests, f"test_reject_course_{state}", state_test(state))


def wallet_test(state, existing=False):
    def test(self):
        if existing:
            self.reservation()
        self.wallet.status = state
        self.wallet.save(update_fields=["status"])
        self.assert_failed(409, reserved="300" if existing else "0", reservations=int(existing))
    return test


for state in (DriverWallet.Status.BLOCKED, DriverWallet.Status.CLOSED):
    for existing in (False, True):
        setattr(CourseWalletAcceptTests, f"test_wallet_{state}_existing_{existing}", wallet_test(state, existing))


def configuration_test(price, rate):
    def test(self):
        self.course.initial_price = Decimal(price)
        self.course.save(update_fields=["initial_price"])
        self.setting.rate = Decimal(rate)
        self.setting.save()
        self.assert_failed(409)
    return test


for name, price, rate in (("zero_price", "0", "15"), ("negative_price", "-1", "15"), ("zero_rate", "2000", "0"), ("rounded_zero", "0.01", "15")):
    setattr(CourseWalletAcceptTests, f"test_configuration_{name}", configuration_test(price, rate))


def driver_test(field):
    def test(self):
        if field == "disabled":
            self.driver.is_enabled = False
            self.driver.save()
        elif field == "deleted":
            self.driver.soft_delete()
        else:
            self.driver.user.is_active = False
            self.driver.user.save()
        self.assert_failed(400)
    return test


for field in ("disabled", "deleted", "inactive_user"):
    setattr(CourseWalletAcceptTests, f"test_driver_{field}", driver_test(field))
