from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Commission, CommissionSetting, Course, Customer, Driver
from core.services.commission_service import (
    CommissionSettlementError,
    confirm_commission_settlement,
)


class CommissionTestMixin:
    def setUp(self):
        user_model = Driver._meta.get_field("user").remote_field.model
        self.superuser = user_model.objects.create_superuser(
            email="super@example.com", phone="+23560000001", password="pass1234"
        )
        self.admin = user_model.objects.create_user(
            email="admin@example.com", phone="+23560000002", password="pass1234",
            user_type="admin", is_staff=True,
        )
        customer_user = user_model.objects.create_user(
            email="customer@example.com", phone="+23560000003", password="pass1234"
        )
        driver_user = user_model.objects.create_user(
            email="driver@example.com", phone="+23560000004", password="pass1234", user_type="driver"
        )
        other_driver_user = user_model.objects.create_user(
            email="driver2@example.com", phone="+23560000005", password="pass1234", user_type="driver"
        )
        self.customer = Customer.objects.create(user=customer_user)
        self.driver = Driver.objects.create(user=driver_user)
        self.other_driver = Driver.objects.create(user=other_driver_user)

    def course(self, *, driver=None, status=Course.Status.COMPLETED, price="3800.00"):
        return Course.objects.create(
            driver=self.driver if driver is None else driver,
            customer=self.customer,
            departure_latitude=Decimal("12.100000"), departure_longitude=Decimal("15.100000"),
            destination_latitude=Decimal("12.200000"), destination_longitude=Decimal("15.200000"),
            starting_landmark="Chagoua", arrival_landmark="Farcha",
            initial_price=Decimal(price), final_price=Decimal(price), status=status,
            completed_at=timezone.now() if status == Course.Status.COMPLETED else None,
        )


class CommissionCreationTests(CommissionTestMixin, TestCase):
    def test_commission_created_for_completed_course_with_exact_calculation(self):
        course = self.course()
        commission = Commission.objects.get(course=course)
        self.assertEqual(commission.gross_amount, Decimal("3800.00"))
        self.assertEqual(commission.commission_rate, Decimal("15.00"))
        self.assertEqual(commission.commission_amount, Decimal("570.00"))
        self.assertEqual(commission.driver_net_amount, Decimal("3230.00"))

    def test_no_commission_for_cancelled_course(self):
        course = self.course(status=Course.Status.CANCELLED)
        self.assertFalse(Commission.objects.filter(course=course).exists())

    def test_repeated_saves_do_not_duplicate(self):
        course = self.course()
        course.save()
        course.save()
        self.assertEqual(Commission.objects.filter(course=course).count(), 1)

    def test_rate_change_does_not_modify_existing_commission(self):
        first = self.course()
        setting = CommissionSetting.objects.get()
        setting.rate = Decimal("18.00")
        setting.save()
        second = self.course(price="1000.00")
        self.assertEqual(Commission.objects.get(course=first).commission_rate, Decimal("15.00"))
        self.assertEqual(Commission.objects.get(course=second).commission_rate, Decimal("18.00"))


class CommissionSettlementTests(CommissionTestMixin, TestCase):
    def test_confirm_multiple_commissions(self):
        commissions = [Commission.objects.get(course=self.course(price=value)) for value in ("1000", "2000")]
        settlement = confirm_commission_settlement(
            self.driver, [item.id for item in commissions], "cash", "VERSEMENT-001",
            timezone.now(), self.superuser,
        )
        self.assertEqual(settlement.total_amount, Decimal("450.00"))
        self.assertEqual(Commission.objects.filter(status="paid", settlement=settlement).count(), 2)

    def test_refuses_commission_from_another_driver_and_rolls_back(self):
        own = Commission.objects.get(course=self.course())
        other = Commission.objects.get(course=self.course(driver=self.other_driver))
        with self.assertRaises(CommissionSettlementError):
            confirm_commission_settlement(
                self.driver, [own.id, other.id], "cash", "X", timezone.now(), self.superuser
            )
        own.refresh_from_db()
        self.assertEqual(own.status, Commission.Status.PENDING)

    def test_refuses_already_paid_commission(self):
        commission = Commission.objects.get(course=self.course())
        confirm_commission_settlement(
            self.driver, [commission.id], "cash", "X", timezone.now(), self.superuser
        )
        with self.assertRaises(CommissionSettlementError):
            confirm_commission_settlement(
                self.driver, [commission.id], "cash", "Y", timezone.now(), self.superuser
            )

    def test_refuses_non_superuser(self):
        commission = Commission.objects.get(course=self.course())
        with self.assertRaises(PermissionDenied):
            confirm_commission_settlement(
                self.driver, [commission.id], "cash", "X", timezone.now(), self.admin
            )


class CommissionAPITests(CommissionTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_staff_can_read_but_only_superuser_can_update_rate(self):
        url = reverse("commission-settings-current")
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.patch(url, {"rate": "18.00"}, format="json").status_code, 403)
        self.client.force_authenticate(self.superuser)
        self.assertEqual(self.client.patch(url, {"rate": "18.00"}, format="json").status_code, 200)

    def test_settlement_confirmation_permissions(self):
        commission = Commission.objects.get(course=self.course())
        url = reverse("commission-settlements-confirm")
        payload = {"driver_id": self.driver.id, "commission_ids": [commission.id], "payment_mode": "cash"}
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 403)
        self.client.force_authenticate(self.superuser)
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 201)

    def test_dashboard_commission_statistics(self):
        commission = Commission.objects.get(course=self.course())
        confirm_commission_settlement(
            self.driver, [commission.id], "cash", "X", timezone.now(), self.superuser
        )
        self.client.force_authenticate(self.admin)
        response = self.client.get(reverse("dashboard-stats"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.data["gross_course_volume"])), Decimal("3800.00"))
        self.assertEqual(Decimal(str(response.data["commissions_generated"])), Decimal("570.00"))
        self.assertEqual(Decimal(str(response.data["djina_revenue_collected"])), Decimal("570.00"))
        self.assertEqual(Decimal(str(response.data["commissions_pending"])), Decimal("0"))
        self.assertEqual(Decimal(str(response.data["drivers_net_revenue"])), Decimal("3230.00"))


class BackfillCommissionCommandTests(CommissionTestMixin, TestCase):
    def test_dry_run_then_apply_is_idempotent(self):
        course = self.course(status=Course.Status.REQUESTED)
        Course.objects.filter(pk=course.pk).update(status=Course.Status.COMPLETED, completed_at=timezone.now())
        output = StringIO()
        call_command("backfill_commissions", dry_run=True, stdout=output)
        self.assertIn("1 commission(s) would be created", output.getvalue())
        self.assertFalse(Commission.objects.filter(course=course).exists())
        call_command("backfill_commissions", apply=True, stdout=StringIO())
        call_command("backfill_commissions", apply=True, stdout=StringIO())
        self.assertEqual(Commission.objects.filter(course=course).count(), 1)
