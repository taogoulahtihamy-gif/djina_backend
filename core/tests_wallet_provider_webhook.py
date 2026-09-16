import json
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse

from rest_framework.test import APITestCase

from core.models import (
    CustomUser,
    Driver,
    DriverWallet,
    WalletTopUp,
    WalletTransaction,
)
from core.services.wallet_provider_service import (
    build_mock_provider_signature,
)
from core.services.wallet_topup_service import (
    request_wallet_topup,
)


@override_settings(
    WALLET_MOCK_PROVIDER_ENABLED=True,
    WALLET_MOCK_PROVIDER_SECRET="djina-test-secret",
)
class WalletProviderWebhookTests(APITestCase):

    def setUp(self):
        user = CustomUser.objects.create_user(
            email="provider-webhook@example.com",
            phone="+23567777001",
            user_type="driver",
        )

        self.driver = Driver.objects.create(
            user=user,
            is_enabled=True,
        )

        self.wallet = DriverWallet.objects.create(
            driver=self.driver,
            balance=Decimal("1000.00"),
        )

        self.topup, _ = request_wallet_topup(
            wallet=self.wallet,
            amount="10000.00",
            provider=WalletTopUp.Provider.AIRTEL_MONEY,
            phone="+23566000000",
            idempotency_key="provider-request-001",
        )

        self.url = reverse(
            "wallet-mock-webhook"
        )

    def payload(self, **changes):
        data = {
            "topup_id": self.topup.pk,
            "provider":
                WalletTopUp.Provider.AIRTEL_MONEY,
            "provider_reference":
                "provider-ref-001",
            "amount": "10000.00",
            "status":
                WalletTopUp.Status.SUCCESS,
        }

        data.update(changes)

        return data

    def send(self, payload, *, signature=True):
        body = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        headers = {}

        if signature is True:
            headers["HTTP_X_DJINA_SIGNATURE"] = (
                build_mock_provider_signature(
                    body,
                    "djina-test-secret",
                )
            )

        elif isinstance(signature, str):
            headers["HTTP_X_DJINA_SIGNATURE"] = (
                signature
            )

        return self.client.generic(
            "POST",
            self.url,
            data=body,
            content_type="application/json",
            **headers,
        )

    def test_success_callback_credits_wallet(self):
        response = self.send(
            self.payload()
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.wallet.refresh_from_db()
        self.topup.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("11000.00"),
        )

        self.assertEqual(
            self.topup.status,
            WalletTopUp.Status.SUCCESS,
        )

        self.assertEqual(
            WalletTransaction.objects.count(),
            1,
        )

    def test_duplicate_success_callback_is_idempotent(self):
        first = self.send(
            self.payload()
        )

        second = self.send(
            self.payload()
        )

        self.assertEqual(
            first.status_code,
            200,
        )

        self.assertEqual(
            second.status_code,
            200,
        )

        self.assertTrue(
            first.data["processed"]
        )

        self.assertFalse(
            second.data["processed"]
        )

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("11000.00"),
        )

        self.assertEqual(
            WalletTransaction.objects.count(),
            1,
        )

    def test_missing_signature_is_rejected(self):
        response = self.send(
            self.payload(),
            signature=False,
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

    def test_wrong_signature_is_rejected(self):
        response = self.send(
            self.payload(),
            signature="bad-signature",
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.assertFalse(
            WalletTransaction.objects.exists()
        )

    def test_wrong_amount_does_not_credit(self):
        response = self.send(
            self.payload(
                amount="9000.00"
            )
        )

        self.assertEqual(
            response.status_code,
            409,
        )

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

    def test_failed_callback_marks_topup_failed(self):
        response = self.send(
            self.payload(
                status=WalletTopUp.Status.FAILED,
                failure_reason="provider_declined",
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.wallet.refresh_from_db()
        self.topup.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

        self.assertEqual(
            self.topup.status,
            WalletTopUp.Status.FAILED,
        )

        self.assertEqual(
            self.topup.failure_reason,
            "provider_declined",
        )

        self.assertFalse(
            WalletTransaction.objects.exists()
        )

    def test_duplicate_failed_callback_is_idempotent(self):
        payload = self.payload(
            status=WalletTopUp.Status.FAILED,
            failure_reason="provider_declined",
        )

        first = self.send(payload)
        second = self.send(payload)

        self.assertEqual(
            first.status_code,
            200,
        )

        self.assertEqual(
            second.status_code,
            200,
        )

        self.assertTrue(
            first.data["processed"]
        )

        self.assertFalse(
            second.data["processed"]
        )

        self.assertFalse(
            WalletTransaction.objects.exists()
        )

    def test_failed_then_success_is_rejected(self):
        failed = self.payload(
            status=WalletTopUp.Status.FAILED,
            failure_reason="provider_declined",
        )

        self.assertEqual(
            self.send(failed).status_code,
            200,
        )

        response = self.send(
            self.payload()
        )

        self.assertEqual(
            response.status_code,
            409,
        )

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

    def test_unknown_topup_is_rejected(self):
        response = self.send(
            self.payload(
                topup_id=999999
            )
        )

        self.assertEqual(
            response.status_code,
            400,
        )

    def test_invalid_status_is_rejected(self):
        response = self.send(
            self.payload(
                status="magic_success"
            )
        )

        self.assertEqual(
            response.status_code,
            400,
        )

    def test_authenticated_driver_without_signature_still_rejected(self):
        self.client.force_authenticate(
            self.driver.user
        )

        response = self.send(
            self.payload(),
            signature=False,
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("1000.00"),
        )

    def test_get_is_not_allowed(self):
        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            405,
        )

    @override_settings(
        WALLET_MOCK_PROVIDER_ENABLED=False
    )
    def test_mock_webhook_disabled_by_default_policy(self):
        response = self.send(
            self.payload()
        )

        self.assertEqual(
            response.status_code,
            404,
        )
