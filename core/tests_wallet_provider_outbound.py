from unittest.mock import Mock

import requests
from django.test import SimpleTestCase

from core.services.wallet_provider_auth import (
    NoAuthWalletProviderStrategy,
)
from core.services.wallet_provider_client import (
    WalletProviderAPIClient,
)
from core.services.wallet_provider_http import (
    WalletProviderHTTPClient,
)
from core.services.wallet_provider_outbound import (
    BaseWalletProviderOutgoingAdapter,
    WalletProviderHTTPRequestSpec,
    WalletProviderOutboundProtocolError,
    WalletProviderOutboundRejectedError,
    WalletProviderOutboundService,
    WalletProviderOutboundTimeoutError,
    WalletProviderOutboundUnavailableError,
    WalletProviderOutboundValidationError,
    WalletProviderTopUpInitiationResult,
)


class TestOutgoingAdapter(
    BaseWalletProviderOutgoingAdapter
):
    def __init__(self):
        self.last_command = None

    def build_topup_request(
        self,
        command,
    ):
        self.last_command = command

        return WalletProviderHTTPRequestSpec(
            method="POST",
            path="topups",
            headers={
                "X-Idempotency-Key":
                    command.idempotency_key,
                "X-Correlation-ID":
                    command.correlation_id,
            },
            json_body={
                "amount": str(
                    command.amount
                ),
                "currency":
                    command.currency,
                "phone":
                    command.phone,
            },
        )

    def parse_topup_response(
        self,
        *,
        response,
        command,
    ):
        return WalletProviderTopUpInitiationResult(
            provider_reference=(
                response.data[
                    "provider_reference"
                ]
            ),
            provider_status=(
                response.data["status"]
            ),
            raw_data=response.data,
        )


class InvalidResultAdapter(
    TestOutgoingAdapter
):
    def parse_topup_response(
        self,
        *,
        response,
        command,
    ):
        return {
            "provider_reference": "bad"
        }


class ExplodingBuildAdapter(
    TestOutgoingAdapter
):
    def build_topup_request(
        self,
        command,
    ):
        raise RuntimeError("boom")


class WalletProviderOutboundServiceTests(
    SimpleTestCase
):

    def setUp(self):
        self.session = Mock()

        self.http = WalletProviderHTTPClient(
            base_url=(
                "https://sandbox.example.test/api"
            ),
            timeout_seconds=10,
            session=self.session,
        )

        self.api = WalletProviderAPIClient(
            http_client=self.http,
            auth_strategy=(
                NoAuthWalletProviderStrategy()
            ),
        )

        self.adapter = TestOutgoingAdapter()

        self.service = (
            WalletProviderOutboundService(
                api_client=self.api,
                adapter=self.adapter,
            )
        )

    def response(
        self,
        *,
        status_code=200,
        data=None,
    ):
        response = Mock()
        response.status_code = status_code
        response.headers = {
            "Content-Type":
                "application/json",
        }
        response.json.return_value = (
            {
                "provider_reference":
                    "remote-001",
                "status": "pending",
            }
            if data is None
            else data
        )
        return response

    def values(self, **changes):
        data = {
            "topup_id": 12,
            "amount": "10000.00",
            "currency": "XAF",
            "phone": "+23566000000",
            "idempotency_key":
                "djina-topup-12",
            "correlation_id":
                "corr-12",
        }
        data.update(changes)
        return data

    def test_initiation_uses_adapter_and_http_client(self):
        self.session.request.return_value = (
            self.response()
        )

        result = self.service.initiate_topup(
            **self.values()
        )

        self.assertEqual(
            result.provider_reference,
            "remote-001",
        )

        self.assertEqual(
            result.provider_status,
            "pending",
        )

        self.assertEqual(
            self.adapter.last_command.topup_id,
            12,
        )

    def test_idempotency_key_reaches_adapter(self):
        self.session.request.return_value = (
            self.response()
        )

        self.service.initiate_topup(
            **self.values(
                idempotency_key="stable-key",
            )
        )

        headers = (
            self.session.request
            .call_args
            .kwargs["headers"]
        )

        self.assertEqual(
            headers["X-Idempotency-Key"],
            "stable-key",
        )

    def test_correlation_id_reaches_adapter(self):
        self.session.request.return_value = (
            self.response()
        )

        self.service.initiate_topup(
            **self.values(
                correlation_id="corr-999",
            )
        )

        headers = (
            self.session.request
            .call_args
            .kwargs["headers"]
        )

        self.assertEqual(
            headers["X-Correlation-ID"],
            "corr-999",
        )

    def test_correlation_id_is_generated(self):
        self.session.request.return_value = (
            self.response()
        )

        values = self.values()
        values["correlation_id"] = None

        self.service.initiate_topup(
            **values
        )

        correlation_id = (
            self.adapter
            .last_command
            .correlation_id
        )

        self.assertTrue(
            correlation_id
        )

        self.assertLessEqual(
            len(correlation_id),
            120,
        )

    def test_amount_is_normalized(self):
        self.session.request.return_value = (
            self.response()
        )

        self.service.initiate_topup(
            **self.values(
                amount="10000.00",
            )
        )

        self.assertEqual(
            str(
                self.adapter
                .last_command
                .amount
            ),
            "10000.00",
        )

    def test_invalid_amount_rejected_before_network(self):
        with self.assertRaises(
            WalletProviderOutboundValidationError
        ):
            self.service.initiate_topup(
                **self.values(
                    amount="0.00",
                )
            )

        self.session.request.assert_not_called()

    def test_timeout_is_mapped(self):
        self.session.request.side_effect = (
            requests.Timeout("timeout")
        )

        with self.assertRaises(
            WalletProviderOutboundTimeoutError
        ):
            self.service.initiate_topup(
                **self.values()
            )

    def test_network_error_is_mapped(self):
        self.session.request.side_effect = (
            requests.ConnectionError(
                "network down"
            )
        )

        with self.assertRaises(
            WalletProviderOutboundUnavailableError
        ):
            self.service.initiate_topup(
                **self.values()
            )

    def test_http_rejection_is_mapped(self):
        self.session.request.return_value = (
            self.response(
                status_code=409
            )
        )

        with self.assertRaises(
            WalletProviderOutboundRejectedError
        ) as context:
            self.service.initiate_topup(
                **self.values()
            )

        self.assertEqual(
            context.exception.status_code,
            409,
        )

    def test_invalid_json_is_mapped(self):
        response = self.response()
        response.json.side_effect = (
            ValueError("invalid json")
        )

        self.session.request.return_value = (
            response
        )

        with self.assertRaises(
            WalletProviderOutboundProtocolError
        ):
            self.service.initiate_topup(
                **self.values()
            )

    def test_invalid_adapter_result_rejected(self):
        service = WalletProviderOutboundService(
            api_client=self.api,
            adapter=InvalidResultAdapter(),
        )

        self.session.request.return_value = (
            self.response()
        )

        with self.assertRaises(
            WalletProviderOutboundProtocolError
        ):
            service.initiate_topup(
                **self.values()
            )

    def test_adapter_build_failure_is_wrapped(self):
        service = WalletProviderOutboundService(
            api_client=self.api,
            adapter=ExplodingBuildAdapter(),
        )

        with self.assertRaises(
            WalletProviderOutboundProtocolError
        ):
            service.initiate_topup(
                **self.values()
            )

        self.session.request.assert_not_called()

    def test_invalid_adapter_rejected(self):
        with self.assertRaises(
            WalletProviderOutboundValidationError
        ):
            WalletProviderOutboundService(
                api_client=self.api,
                adapter=object(),
            )
