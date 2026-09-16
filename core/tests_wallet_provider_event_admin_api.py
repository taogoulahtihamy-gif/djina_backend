from decimal import Decimal

from django.urls import reverse
from rest_framework.test import (
    APITestCase,
)

from core.models import (
    CustomUser,
    Driver,
    DriverWallet,
    WalletProviderEvent,
    WalletTopUp,
)


class WalletProviderEventAdminAPITests(
    APITestCase
):

    def setUp(self):
        self.admin = (
            CustomUser.objects.create_user(
                email=(
                    "wallet-event-admin"
                    "@example.com"
                ),
                phone="+23567779601",
                user_type="admin",
            )
        )

        self.admin.is_staff = True

        self.admin.save(
            update_fields=[
                "is_staff",
            ]
        )

        self.driver_user = (
            CustomUser.objects.create_user(
                email=(
                    "wallet-event-driver"
                    "@example.com"
                ),
                phone="+23567779602",
                user_type="driver",
            )
        )

        driver = Driver.objects.create(
            user=self.driver_user,
            is_enabled=True,
        )

        wallet = DriverWallet.objects.create(
            driver=driver,
            balance=Decimal("5000.00"),
        )

        self.topup_one = (
            WalletTopUp.objects.create(
                wallet=wallet,
                amount=Decimal(
                    "10000.00"
                ),
                currency="XAF",
                provider=(
                    WalletTopUp
                    .Provider
                    .AIRTEL_MONEY
                ),
                phone="+23566000401",
                provider_reference=
                    "airtel-ref-001",
                provider_status=
                    "COMPLETED",
                idempotency_key=
                    "admin-event-topup-1",
                status=(
                    WalletTopUp
                    .Status
                    .SUCCESS
                ),
            )
        )

        self.topup_two = (
            WalletTopUp.objects.create(
                wallet=wallet,
                amount=Decimal(
                    "8000.00"
                ),
                currency="XAF",
                provider=(
                    WalletTopUp
                    .Provider
                    .MOOV_MONEY
                ),
                phone="+23566000402",
                provider_reference=
                    "moov-ref-002",
                provider_status=
                    "DECLINED",
                idempotency_key=
                    "admin-event-topup-2",
                status=(
                    WalletTopUp
                    .Status
                    .FAILED
                ),
            )
        )

        self.event_one = (
            WalletProviderEvent.objects.create(
                topup=self.topup_one,
                reported_topup_id=
                    self.topup_one.pk,
                provider=(
                    WalletTopUp
                    .Provider
                    .AIRTEL_MONEY
                ),
                provider_reference=
                    "airtel-ref-001",
                callback_status=
                    WalletTopUp
                    .Status
                    .SUCCESS,
                provider_status=
                    "COMPLETED",
                amount=Decimal(
                    "10000.00"
                ),
                outcome=(
                    WalletProviderEvent
                    .Outcome
                    .ACCEPTED
                ),
                processed=True,
            )
        )

        self.event_two = (
            WalletProviderEvent.objects.create(
                topup=self.topup_two,
                reported_topup_id=
                    self.topup_two.pk,
                provider=(
                    WalletTopUp
                    .Provider
                    .MOOV_MONEY
                ),
                provider_reference=
                    "moov-ref-002",
                callback_status=
                    WalletTopUp
                    .Status
                    .FAILED,
                provider_status=
                    "DECLINED",
                amount=Decimal(
                    "8000.00"
                ),
                failure_reason=
                    "Insufficient funds.",
                outcome=(
                    WalletProviderEvent
                    .Outcome
                    .ACCEPTED
                ),
                processed=True,
            )
        )

        self.event_three = (
            WalletProviderEvent.objects.create(
                topup=None,
                reported_topup_id=999999,
                provider=(
                    WalletTopUp
                    .Provider
                    .AIRTEL_MONEY
                ),
                provider_reference=
                    "unknown-ref-003",
                callback_status=
                    WalletTopUp
                    .Status
                    .SUCCESS,
                provider_status=
                    "COMPLETED",
                amount=Decimal(
                    "12000.00"
                ),
                outcome=(
                    WalletProviderEvent
                    .Outcome
                    .REJECTED
                ),
                processed=False,
                error_type=
                    "WalletTopUpError",
                error_message=
                    "Top-up request does not exist.",
            )
        )

    def list_url(self):
        return reverse(
            "wallet-provider-events-list"
        )

    def detail_url(
        self,
        event,
    ):
        return reverse(
            "wallet-provider-events-detail",
            kwargs={
                "pk": event.pk,
            },
        )

    def authenticate_admin(self):
        self.client.force_authenticate(
            self.admin
        )

    @staticmethod
    def rows(response):
        data = response.data

        if (
            isinstance(data, dict)
            and "results" in data
        ):
            return data["results"]

        return data

    def test_admin_can_list_events(self):
        self.authenticate_admin()

        response = self.client.get(
            self.list_url()
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            len(self.rows(response)),
            3,
        )

    def test_admin_can_read_event_detail(self):
        self.authenticate_admin()

        response = self.client.get(
            self.detail_url(
                self.event_one
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data["id"],
            self.event_one.pk,
        )

        self.assertEqual(
            response.data[
                "provider_reference"
            ],
            "airtel-ref-001",
        )

    def test_driver_is_forbidden(self):
        self.client.force_authenticate(
            self.driver_user
        )

        response = self.client.get(
            self.list_url()
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_unauthenticated_access_is_denied(self):
        response = self.client.get(
            self.list_url()
        )

        self.assertIn(
            response.status_code,
            (401, 403),
        )

    def test_api_is_strictly_read_only(self):
        self.authenticate_admin()

        post_response = self.client.post(
            self.list_url(),
            {},
            format="json",
        )

        patch_response = self.client.patch(
            self.detail_url(
                self.event_one
            ),
            {
                "outcome": "rejected",
            },
            format="json",
        )

        delete_response = self.client.delete(
            self.detail_url(
                self.event_one
            )
        )

        self.assertEqual(
            post_response.status_code,
            405,
        )

        self.assertEqual(
            patch_response.status_code,
            405,
        )

        self.assertEqual(
            delete_response.status_code,
            405,
        )

        self.event_one.refresh_from_db()

        self.assertEqual(
            self.event_one.outcome,
            WalletProviderEvent
            .Outcome
            .ACCEPTED,
        )

    def test_filter_by_provider(self):
        self.authenticate_admin()

        response = self.client.get(
            self.list_url(),
            {
                "provider":
                    WalletTopUp
                    .Provider
                    .MOOV_MONEY,
            },
        )

        rows = self.rows(response)

        self.assertEqual(
            len(rows),
            1,
        )

        self.assertEqual(
            rows[0]["id"],
            self.event_two.pk,
        )

    def test_filter_by_outcome(self):
        self.authenticate_admin()

        response = self.client.get(
            self.list_url(),
            {
                "outcome":
                    WalletProviderEvent
                    .Outcome
                    .REJECTED,
            },
        )

        rows = self.rows(response)

        self.assertEqual(
            len(rows),
            1,
        )

        self.assertEqual(
            rows[0]["id"],
            self.event_three.pk,
        )

    def test_filter_by_topup_uses_reported_id(self):
        self.authenticate_admin()

        response = self.client.get(
            self.list_url(),
            {
                "topup": 999999,
            },
        )

        rows = self.rows(response)

        self.assertEqual(
            len(rows),
            1,
        )

        self.assertEqual(
            rows[0]["id"],
            self.event_three.pk,
        )

        self.assertIsNone(
            rows[0]["topup_id"]
        )

        self.assertEqual(
            rows[0][
                "reported_topup_id"
            ],
            999999,
        )

    def test_filter_by_provider_reference(self):
        self.authenticate_admin()

        response = self.client.get(
            self.list_url(),
            {
                "provider_reference":
                    "airtel-ref-001",
            },
        )

        rows = self.rows(response)

        self.assertEqual(
            len(rows),
            1,
        )

        self.assertEqual(
            rows[0]["id"],
            self.event_one.pk,
        )

    def test_filters_can_be_combined(self):
        self.authenticate_admin()

        response = self.client.get(
            self.list_url(),
            {
                "provider":
                    WalletTopUp
                    .Provider
                    .AIRTEL_MONEY,
                "outcome":
                    WalletProviderEvent
                    .Outcome
                    .REJECTED,
            },
        )

        rows = self.rows(response)

        self.assertEqual(
            len(rows),
            1,
        )

        self.assertEqual(
            rows[0]["id"],
            self.event_three.pk,
        )

    def test_invalid_provider_returns_400(self):
        self.authenticate_admin()

        response = self.client.get(
            self.list_url(),
            {
                "provider":
                    "fake-provider",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )

    def test_invalid_outcome_returns_400(self):
        self.authenticate_admin()

        response = self.client.get(
            self.list_url(),
            {
                "outcome":
                    "destroyed",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )

    def test_invalid_topup_filter_returns_400(self):
        self.authenticate_admin()

        response = self.client.get(
            self.list_url(),
            {
                "topup":
                    "not-an-id",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )

    def test_serializer_exposes_no_raw_secrets(self):
        self.authenticate_admin()

        response = self.client.get(
            self.detail_url(
                self.event_one
            )
        )

        keys = set(
            response.data.keys()
        )

        self.assertNotIn(
            "headers",
            keys,
        )

        self.assertNotIn(
            "raw_payload",
            keys,
        )

        self.assertNotIn(
            "signature",
            keys,
        )

        self.assertNotIn(
            "secret",
            keys,
        )
