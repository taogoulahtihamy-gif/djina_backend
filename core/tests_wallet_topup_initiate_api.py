from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse

from rest_framework.test import APITestCase

from core.models import (
    Customer,
    CustomUser,
    Driver,
    DriverWallet,
    WalletTopUp,
    WalletTransaction,
)
from core.services.wallet_topup_initiation_service import (
    WalletTopUpInitiationConflictError,
    WalletTopUpInitiationConsistencyError,
    WalletTopUpInitiationError,
    WalletTopUpInitiationProviderError,
    WalletTopUpInitiationResult,
    WalletTopUpInitiationStateError,
)


class WalletTopUpInitiateAPITests(APITestCase):

    def setUp(self):
        self.driver_user = CustomUser.objects.create_user(
            email="init-api-driver@example.com",
            phone="+23567779101",
            user_type="driver",
        )

        self.driver = Driver.objects.create(
            user=self.driver_user,
            is_enabled=True,
        )

        self.wallet = DriverWallet.objects.create(
            driver=self.driver,
            balance=Decimal("2000.00"),
        )

        self.topup = WalletTopUp.objects.create(
            wallet=self.wallet,
            amount=Decimal("10000.00"),
            currency="XAF",
            provider=WalletTopUp.Provider.AIRTEL_MONEY,
            phone="+23566000000",
            idempotency_key="api-init-topup-001",
            status=WalletTopUp.Status.PENDING,
        )

        self.other_user = CustomUser.objects.create_user(
            email="init-api-other@example.com",
            phone="+23567779102",
            user_type="driver",
        )

        self.other_driver = Driver.objects.create(
            user=self.other_user,
            is_enabled=True,
        )

        self.other_wallet = DriverWallet.objects.create(
            driver=self.other_driver,
            balance=Decimal("5000.00"),
        )

        self.other_topup = WalletTopUp.objects.create(
            wallet=self.other_wallet,
            amount=Decimal("8000.00"),
            currency="XAF",
            provider=WalletTopUp.Provider.AIRTEL_MONEY,
            phone="+23566000001",
            idempotency_key="api-init-other-001",
            status=WalletTopUp.Status.PENDING,
        )

        self.customer_user = CustomUser.objects.create_user(
            email="init-api-customer@example.com",
            phone="+23567779103",
            user_type="customer",
        )

        Customer.objects.create(
            user=self.customer_user
        )

    def url(self, topup=None):
        return reverse(
            "wallet-topup-initiate",
            kwargs={
                "topup_id":
                    (topup or self.topup).pk
            },
        )

    def auth_driver(self):
        self.client.force_authenticate(
            self.driver_user
        )

    def fake_success(self, *, initiated=True):
        self.topup.provider_reference = (
            "remote-ref-001"
        )
        self.topup.save(
            update_fields=[
                "provider_reference",
                "updated_at",
            ]
        )

        return WalletTopUpInitiationResult(
            topup=self.topup,
            provider_reference="remote-ref-001",
            provider_status="pending",
            initiated=initiated,
        )

    @patch(
        "core.views_wallet.initiate_wallet_topup"
    )
    def test_owner_can_initiate_topup(
        self,
        mocked_initiate,
    ):
        mocked_initiate.return_value = (
            self.fake_success()
        )

        self.auth_driver()

        response = self.client.post(
            self.url(),
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertTrue(
            response.data["initiated"]
        )

        self.assertEqual(
            response.data["provider_reference"],
            "remote-ref-001",
        )

        self.assertEqual(
            response.data["provider_status"],
            "pending",
        )

        mocked_initiate.assert_called_once_with(
            topup_id=self.topup.pk,
        )

    @patch(
        "core.views_wallet.initiate_wallet_topup"
    )
    def test_idempotent_retry_is_exposed(
        self,
        mocked_initiate,
    ):
        mocked_initiate.return_value = (
            self.fake_success(
                initiated=False
            )
        )

        self.auth_driver()

        response = self.client.post(
            self.url(),
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertFalse(
            response.data["initiated"]
        )

    @patch(
        "core.views_wallet.initiate_wallet_topup"
    )
    def test_other_driver_topup_is_hidden(
        self,
        mocked_initiate,
    ):
        self.auth_driver()

        response = self.client.post(
            self.url(self.other_topup),
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        mocked_initiate.assert_not_called()

    @patch(
        "core.views_wallet.initiate_wallet_topup"
    )
    def test_terminal_state_maps_to_409(
        self,
        mocked_initiate,
    ):
        mocked_initiate.side_effect = (
            WalletTopUpInitiationStateError(
                "Only pending top-ups can be initiated."
            )
        )

        self.auth_driver()

        response = self.client.post(
            self.url(),
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            409,
        )

    @patch(
        "core.views_wallet.initiate_wallet_topup"
    )
    def test_conflict_maps_to_409(
        self,
        mocked_initiate,
    ):
        mocked_initiate.side_effect = (
            WalletTopUpInitiationConflictError(
                "Provider reference conflict."
            )
        )

        self.auth_driver()

        response = self.client.post(
            self.url(),
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            409,
        )

    @patch(
        "core.views_wallet.initiate_wallet_topup"
    )
    def test_consistency_error_maps_to_409(
        self,
        mocked_initiate,
    ):
        mocked_initiate.side_effect = (
            WalletTopUpInitiationConsistencyError(
                "Provider reference mismatch."
            )
        )

        self.auth_driver()

        response = self.client.post(
            self.url(),
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            409,
        )

    @patch(
        "core.views_wallet.initiate_wallet_topup"
    )
    def test_provider_unavailable_maps_to_503(
        self,
        mocked_initiate,
    ):
        mocked_initiate.side_effect = (
            WalletTopUpInitiationProviderError(
                "Wallet provider runtime is unavailable."
            )
        )

        self.auth_driver()

        response = self.client.post(
            self.url(),
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            503,
        )

    @patch(
        "core.views_wallet.initiate_wallet_topup"
    )
    def test_generic_initiation_error_maps_to_400(
        self,
        mocked_initiate,
    ):
        mocked_initiate.side_effect = (
            WalletTopUpInitiationError(
                "Invalid top-up."
            )
        )

        self.auth_driver()

        response = self.client.post(
            self.url(),
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            400,
        )

    def test_customer_cannot_initiate_topup(self):
        self.client.force_authenticate(
            self.customer_user
        )

        response = self.client.post(
            self.url(),
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_unauthenticated_user_cannot_initiate(self):
        response = self.client.post(
            self.url(),
            {},
            format="json",
        )

        self.assertIn(
            response.status_code,
            (401, 403),
        )

    def test_get_is_not_allowed(self):
        self.auth_driver()

        response = self.client.get(
            self.url()
        )

        self.assertEqual(
            response.status_code,
            405,
        )

    @override_settings(
        WALLET_AIRTEL_ENABLED=False
    )
    def test_unimplemented_provider_does_not_touch_money(self):
        self.auth_driver()

        response = self.client.post(
            self.url(),
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            503,
        )

        self.wallet.refresh_from_db()
        self.topup.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            Decimal("2000.00"),
        )

        self.assertEqual(
            self.wallet.reserved_balance,
            Decimal("0.00"),
        )

        self.assertEqual(
            self.topup.status,
            WalletTopUp.Status.PENDING,
        )

        self.assertIsNone(
            self.topup.provider_reference
        )

        self.assertFalse(
            WalletTransaction.objects.exists()
        )
