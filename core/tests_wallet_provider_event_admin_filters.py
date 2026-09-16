from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from rest_framework.test import (
    APITestCase,
)

from core.models import (
    CustomUser,
    WalletProviderEvent,
    WalletTopUp,
)


class WalletProviderEventAdminFilterTests(
    APITestCase
):

    def setUp(self):
        self.admin = (
            CustomUser.objects.create_user(
                email=(
                    "event-filter-admin"
                    "@example.com"
                ),
                phone="+23567779701",
                user_type="admin",
            )
        )

        self.admin.is_staff = True
        self.admin.save(
            update_fields=[
                "is_staff",
            ]
        )

        self.client.force_authenticate(
            self.admin
        )

        self.now = timezone.now()

        self.success = (
            WalletProviderEvent.objects.create(
                topup=None,
                reported_topup_id=101,
                provider=(
                    WalletTopUp
                    .Provider
                    .AIRTEL_MONEY
                ),
                provider_reference=
                    "filter-success",
                callback_status=
                    WalletTopUp
                    .Status
                    .SUCCESS,
                provider_status=
                    "COMPLETED",
                amount=Decimal(
                    "5000.00"
                ),
                outcome=(
                    WalletProviderEvent
                    .Outcome
                    .ACCEPTED
                ),
                processed=True,
            )
        )

        self.failed = (
            WalletProviderEvent.objects.create(
                topup=None,
                reported_topup_id=102,
                provider=(
                    WalletTopUp
                    .Provider
                    .MOOV_MONEY
                ),
                provider_reference=
                    "filter-failed",
                callback_status=
                    WalletTopUp
                    .Status
                    .FAILED,
                provider_status=
                    "DECLINED",
                amount=Decimal(
                    "6000.00"
                ),
                outcome=(
                    WalletProviderEvent
                    .Outcome
                    .ACCEPTED
                ),
                processed=True,
                failure_reason=
                    "Insufficient funds.",
            )
        )

        self.rejected = (
            WalletProviderEvent.objects.create(
                topup=None,
                reported_topup_id=103,
                provider=(
                    WalletTopUp
                    .Provider
                    .AIRTEL_MONEY
                ),
                provider_reference=
                    "filter-rejected",
                callback_status=
                    WalletTopUp
                    .Status
                    .SUCCESS,
                provider_status=
                    "COMPLETED",
                amount=Decimal(
                    "7000.00"
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
                    "Invalid top-up.",
            )
        )

        WalletProviderEvent.objects.filter(
            pk=self.success.pk
        ).update(
            created_at=(
                self.now
                - timedelta(days=2)
            )
        )

        WalletProviderEvent.objects.filter(
            pk=self.failed.pk
        ).update(
            created_at=(
                self.now
                - timedelta(days=1)
            )
        )

        WalletProviderEvent.objects.filter(
            pk=self.rejected.pk
        ).update(
            created_at=self.now
        )

    def url(self):
        return reverse(
            "wallet-provider-events-list"
        )

    def rows(self, response):
        self.assertIsInstance(
            response.data,
            dict,
        )

        return response.data[
            "results"
        ]

    def test_list_is_paginated(self):
        response = self.client.get(
            self.url()
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data["count"],
            3,
        )

        self.assertIn(
            "next",
            response.data,
        )

        self.assertIn(
            "previous",
            response.data,
        )

        self.assertEqual(
            len(
                response.data[
                    "results"
                ]
            ),
            3,
        )

    def test_page_size_can_be_reduced(self):
        response = self.client.get(
            self.url(),
            {
                "page_size": 1,
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data["count"],
            3,
        )

        self.assertEqual(
            len(
                response.data[
                    "results"
                ]
            ),
            1,
        )

        self.assertIsNotNone(
            response.data["next"]
        )

    def test_filter_callback_status(self):
        response = self.client.get(
            self.url(),
            {
                "callback_status":
                    WalletTopUp
                    .Status
                    .FAILED,
            },
        )

        rows = self.rows(
            response
        )

        self.assertEqual(
            len(rows),
            1,
        )

        self.assertEqual(
            rows[0]["id"],
            self.failed.pk,
        )

    def test_filter_processed_false(self):
        response = self.client.get(
            self.url(),
            {
                "processed":
                    "false",
            },
        )

        rows = self.rows(
            response
        )

        self.assertEqual(
            len(rows),
            1,
        )

        self.assertEqual(
            rows[0]["id"],
            self.rejected.pk,
        )

    def test_filter_error_type(self):
        response = self.client.get(
            self.url(),
            {
                "error_type":
                    "WalletTopUpError",
            },
        )

        rows = self.rows(
            response
        )

        self.assertEqual(
            len(rows),
            1,
        )

        self.assertEqual(
            rows[0]["id"],
            self.rejected.pk,
        )

    def test_filter_created_from(self):
        boundary = (
            self.now
            - timedelta(
                hours=12
            )
        )

        response = self.client.get(
            self.url(),
            {
                "created_from":
                    boundary.isoformat(),
            },
        )

        rows = self.rows(
            response
        )

        self.assertEqual(
            len(rows),
            1,
        )

        self.assertEqual(
            rows[0]["id"],
            self.rejected.pk,
        )

    def test_filter_created_to(self):
        boundary = (
            self.now
            - timedelta(
                hours=36
            )
        )

        response = self.client.get(
            self.url(),
            {
                "created_to":
                    boundary.isoformat(),
            },
        )

        rows = self.rows(
            response
        )

        self.assertEqual(
            len(rows),
            1,
        )

        self.assertEqual(
            rows[0]["id"],
            self.success.pk,
        )

    def test_invalid_callback_status_returns_400(self):
        response = self.client.get(
            self.url(),
            {
                "callback_status":
                    "unknown",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )

    def test_invalid_processed_returns_400(self):
        response = self.client.get(
            self.url(),
            {
                "processed":
                    "maybe",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )

    def test_invalid_date_returns_400(self):
        response = self.client.get(
            self.url(),
            {
                "created_from":
                    "not-a-date",
            },
        )

        self.assertEqual(
            response.status_code,
            400,
        )
