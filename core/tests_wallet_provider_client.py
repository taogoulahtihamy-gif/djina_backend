from unittest.mock import Mock

from django.test import SimpleTestCase

from core.services.wallet_provider_auth import (
    BaseWalletProviderAuthStrategy,
    NoAuthWalletProviderStrategy,
    WalletProviderAuthError,
)
from core.services.wallet_provider_client import (
    WalletProviderAPIClient,
    WalletProviderClientConfigurationError,
)
from core.services.wallet_provider_http import (
    WalletProviderHTTPClient,
    WalletProviderHTTPResponse,
)


class TestBearerAuth(
    BaseWalletProviderAuthStrategy
):
    def __init__(self, token="secret-token"):
        self.token = token

    def get_headers(self):
        return {
            "Authorization":
                f"Bearer {self.token}",
        }


class InvalidHeadersAuth(
    BaseWalletProviderAuthStrategy
):
    def get_headers(self):
        return "not-a-dict"


class ExplodingAuth(
    BaseWalletProviderAuthStrategy
):
    def get_headers(self):
        raise RuntimeError("boom")


class WalletProviderAPIClientTests(SimpleTestCase):

    def setUp(self):
        self.session = Mock()

        self.http = WalletProviderHTTPClient(
            base_url="https://sandbox.example.test/api",
            timeout_seconds=10,
            session=self.session,
        )

        response = Mock()
        response.status_code = 200
        response.headers = {
            "Content-Type": "application/json"
        }
        response.json.return_value = {
            "ok": True
        }

        self.session.request.return_value = response

    def test_auth_headers_are_injected(self):
        client = WalletProviderAPIClient(
            http_client=self.http,
            auth_strategy=TestBearerAuth(),
        )

        client.request_json(
            method="POST",
            path="payments",
        )

        headers = (
            self.session.request
            .call_args
            .kwargs["headers"]
        )

        self.assertEqual(
            headers["Authorization"],
            "Bearer secret-token",
        )

    def test_accept_header_is_preserved(self):
        client = WalletProviderAPIClient(
            http_client=self.http,
            auth_strategy=TestBearerAuth(),
        )

        client.request_json(
            method="GET",
            path="payments",
        )

        headers = (
            self.session.request
            .call_args
            .kwargs["headers"]
        )

        self.assertEqual(
            headers["Accept"],
            "application/json",
        )

    def test_business_headers_are_preserved(self):
        client = WalletProviderAPIClient(
            http_client=self.http,
            auth_strategy=TestBearerAuth(),
        )

        client.request_json(
            method="POST",
            path="payments",
            headers={
                "X-Correlation-ID":
                    "djina-123",
            },
        )

        headers = (
            self.session.request
            .call_args
            .kwargs["headers"]
        )

        self.assertEqual(
            headers["X-Correlation-ID"],
            "djina-123",
        )

        self.assertEqual(
            headers["Authorization"],
            "Bearer secret-token",
        )

    def test_caller_cannot_override_authentication(self):
        client = WalletProviderAPIClient(
            http_client=self.http,
            auth_strategy=TestBearerAuth(
                token="real-token"
            ),
        )

        client.request_json(
            method="POST",
            path="payments",
            headers={
                "Authorization":
                    "Bearer attacker-value",
            },
        )

        headers = (
            self.session.request
            .call_args
            .kwargs["headers"]
        )

        self.assertEqual(
            headers["Authorization"],
            "Bearer real-token",
        )

    def test_original_headers_are_not_mutated(self):
        client = WalletProviderAPIClient(
            http_client=self.http,
            auth_strategy=TestBearerAuth(),
        )

        headers = {
            "Authorization":
                "Bearer original",
            "X-Test": "value",
        }

        client.request_json(
            method="GET",
            path="payments",
            headers=headers,
        )

        self.assertEqual(
            headers,
            {
                "Authorization":
                    "Bearer original",
                "X-Test": "value",
            },
        )

    def test_no_auth_strategy_works(self):
        client = WalletProviderAPIClient(
            http_client=self.http,
            auth_strategy=NoAuthWalletProviderStrategy(),
        )

        result = client.request_json(
            method="GET",
            path="payments",
        )

        self.assertEqual(
            result.data,
            {"ok": True},
        )

    def test_invalid_auth_headers_are_rejected(self):
        client = WalletProviderAPIClient(
            http_client=self.http,
            auth_strategy=InvalidHeadersAuth(),
        )

        with self.assertRaises(
            WalletProviderAuthError
        ):
            client.request_json(
                method="GET",
                path="payments",
            )

        self.session.request.assert_not_called()

    def test_auth_exception_is_wrapped(self):
        client = WalletProviderAPIClient(
            http_client=self.http,
            auth_strategy=ExplodingAuth(),
        )

        with self.assertRaises(
            WalletProviderAuthError
        ):
            client.request_json(
                method="GET",
                path="payments",
            )

        self.session.request.assert_not_called()

    def test_invalid_http_client_rejected(self):
        with self.assertRaises(
            WalletProviderClientConfigurationError
        ):
            WalletProviderAPIClient(
                http_client=object(),
                auth_strategy=NoAuthWalletProviderStrategy(),
            )

    def test_invalid_auth_strategy_rejected(self):
        with self.assertRaises(
            WalletProviderClientConfigurationError
        ):
            WalletProviderAPIClient(
                http_client=self.http,
                auth_strategy=object(),
            )
