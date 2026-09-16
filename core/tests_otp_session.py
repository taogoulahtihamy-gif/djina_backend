from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import (
    AccessToken,
    RefreshToken,
)


User = get_user_model()


class OTPLoginSessionTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.user = User.objects.create_user(
            email="otp.session@djina.local",
            password="StrongPass123!",
            phone="+23566001111",
        )

    @patch(
        "core.views_otp.validate_otp",
        return_value=True,
    )
    def test_otp_login_returns_session_aware_tokens(
        self,
        mocked_validate_otp,
    ):
        response = self.client.post(
            "/api/auth/otp/login/",
            {
                "phone": "+23566001111",
                "code": "123456",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            200,
            response.data,
        )

        self.assertTrue(response.data["success"])

        payload = response.data["data"]

        refresh_raw = payload["refresh"]
        access_raw = payload["access"]

        refresh = RefreshToken(refresh_raw)
        access = AccessToken(access_raw)

        self.assertIn("sid", refresh)
        self.assertIn("sid", access)

        self.assertEqual(
            str(refresh["sid"]),
            str(refresh["jti"]),
        )

        self.assertEqual(
            str(access["sid"]),
            str(refresh["sid"]),
        )

        mocked_validate_otp.assert_called_once_with(
            "+23566001111",
            "123456",
        )

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {access_raw}"
        )

        me_response = self.client.get(
            "/api/auth/me/"
        )

        self.assertEqual(
            me_response.status_code,
            200,
            me_response.data,
        )

        self.user.refresh_from_db()

        self.assertTrue(
            self.user.phone_verified
        )
