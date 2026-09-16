from unittest.mock import Mock

import requests
from django.test import SimpleTestCase

from core.services.wallet_provider_http import (
    WalletProviderHTTPClient,
    WalletProviderHTTPConfigurationError,
    WalletProviderHTTPNetworkError,
    WalletProviderHTTPPayloadError,
    WalletProviderHTTPStatusError,
    WalletProviderHTTPTimeoutError,
    build_wallet_provider_http_client,
)
from core.services.wallet_provider_registry import (
    WalletProviderConfig,
)


class WalletProviderHTTPClientTests(SimpleTestCase):

    def setUp(self):
        self.session = Mock()

        self.client = WalletProviderHTTPClient(
            base_url="https://sandbox.example.test/api",
            timeout_seconds=10,
            session=self.session,
        )

    def response(
        self,
        *,
        status_code=200,
        data=None,
        headers=None,
    ):
        response = Mock()

        response.status_code = status_code
        response.headers = (
            headers
            if headers is not None
            else {
                "Content-Type":
                    "application/json"
            }
        )

        response.json.return_value = (
            {"ok": True}
            if data is None
            else data
        )

        return response

    def test_post_json_uses_configured_origin(self):
        self.session.request.return_value = (
            self.response(
                data={"transaction": "abc"}
            )
        )

        result = self.client.request_json(
            method="POST",
            path="/payments",
            headers={
                "Authorization":
                    "Bearer test-token",
            },
            json_body={
                "amount": "1000.00",
            },
        )

        self.assertEqual(
            result.status_code,
            200,
        )

        self.assertEqual(
            result.data,
            {"transaction": "abc"},
        )

        self.session.request.assert_called_once_with(
            method="POST",
            url=(
                "https://sandbox.example.test/"
                "api/payments"
            ),
            headers={
                "Accept": "application/json",
                "Authorization":
                    "Bearer test-token",
            },
            json={
                "amount": "1000.00",
            },
            params=None,
            timeout=10.0,
        )

    def test_get_forwards_query_parameters(self):
        self.session.request.return_value = (
            self.response(
                data={"status": "pending"}
            )
        )

        result = self.client.request_json(
            method="get",
            path="transactions/abc",
            params={
                "country": "TD",
            },
        )

        self.assertEqual(
            result.data["status"],
            "pending",
        )

        call = self.session.request.call_args

        self.assertEqual(
            call.kwargs["method"],
            "GET",
        )

        self.assertEqual(
            call.kwargs["params"],
            {"country": "TD"},
        )

    def test_original_headers_are_not_mutated(self):
        self.session.request.return_value = (
            self.response()
        )

        headers = {
            "Authorization": "Bearer token",
        }

        self.client.request_json(
            method="POST",
            path="payments",
            headers=headers,
        )

        self.assertEqual(
            headers,
            {
                "Authorization":
                    "Bearer token"
            },
        )

    def test_absolute_path_is_rejected(self):
        with self.assertRaises(
            WalletProviderHTTPConfigurationError
        ):
            self.client.request_json(
                method="POST",
                path=(
                    "https://evil.example.test/"
                    "steal"
                ),
            )

        self.session.request.assert_not_called()

    def test_unsupported_method_is_rejected(self):
        with self.assertRaises(
            WalletProviderHTTPConfigurationError
        ):
            self.client.request_json(
                method="TRACE",
                path="payments",
            )

        self.session.request.assert_not_called()

    def test_invalid_base_url_is_rejected(self):
        with self.assertRaises(
            WalletProviderHTTPConfigurationError
        ):
            WalletProviderHTTPClient(
                base_url="not-a-url",
                timeout_seconds=10,
            )

    def test_credentials_in_base_url_are_rejected(self):
        with self.assertRaises(
            WalletProviderHTTPConfigurationError
        ):
            WalletProviderHTTPClient(
                base_url=(
                    "https://user:password@"
                    "example.test"
                ),
                timeout_seconds=10,
            )

    def test_invalid_timeout_is_rejected(self):
        for value in (
            0,
            -1,
            61,
            True,
            "invalid",
        ):
            with self.subTest(value=value):
                with self.assertRaises(
                    WalletProviderHTTPConfigurationError
                ):
                    WalletProviderHTTPClient(
                        base_url=(
                            "https://example.test"
                        ),
                        timeout_seconds=value,
                    )

    def test_timeout_is_converted_to_domain_error(self):
        self.session.request.side_effect = (
            requests.Timeout(
                "network timeout"
            )
        )

        with self.assertRaises(
            WalletProviderHTTPTimeoutError
        ):
            self.client.request_json(
                method="POST",
                path="payments",
            )

    def test_network_error_is_converted_to_domain_error(self):
        self.session.request.side_effect = (
            requests.ConnectionError(
                "connection failed"
            )
        )

        with self.assertRaises(
            WalletProviderHTTPNetworkError
        ):
            self.client.request_json(
                method="POST",
                path="payments",
            )

    def test_http_4xx_is_rejected(self):
        self.session.request.return_value = (
            self.response(
                status_code=401
            )
        )

        with self.assertRaises(
            WalletProviderHTTPStatusError
        ) as context:
            self.client.request_json(
                method="POST",
                path="payments",
            )

        self.assertEqual(
            context.exception.status_code,
            401,
        )

    def test_http_5xx_is_rejected(self):
        self.session.request.return_value = (
            self.response(
                status_code=503
            )
        )

        with self.assertRaises(
            WalletProviderHTTPStatusError
        ) as context:
            self.client.request_json(
                method="POST",
                path="payments",
            )

        self.assertEqual(
            context.exception.status_code,
            503,
        )

    def test_invalid_json_is_rejected(self):
        response = self.response()
        response.json.side_effect = ValueError(
            "invalid json"
        )

        self.session.request.return_value = (
            response
        )

        with self.assertRaises(
            WalletProviderHTTPPayloadError
        ):
            self.client.request_json(
                method="POST",
                path="payments",
            )

    def test_valid_json_array_is_preserved(self):
        self.session.request.return_value = (
            self.response(
                data=[
                    {"id": 1},
                    {"id": 2},
                ]
            )
        )

        result = self.client.request_json(
            method="GET",
            path="transactions",
        )

        self.assertEqual(
            result.data,
            [
                {"id": 1},
                {"id": 2},
            ],
        )

    def test_client_can_be_built_from_provider_config(self):
        config = WalletProviderConfig(
            key="airtel_money",
            enabled=True,
            environment="sandbox",
            base_url=(
                "https://sandbox.example.test"
            ),
            client_id="client",
            client_secret="secret",
            webhook_secret="webhook",
            timeout_seconds=12.0,
        )

        client = (
            build_wallet_provider_http_client(
                config,
                session=self.session,
            )
        )

        self.assertEqual(
            client.base_url,
            "https://sandbox.example.test/",
        )

        self.assertEqual(
            client.timeout_seconds,
            12.0,
        )

        self.assertIs(
            client.session,
            self.session,
        )
