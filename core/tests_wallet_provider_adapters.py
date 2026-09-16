import json

from django.test import SimpleTestCase

from core.services.wallet_provider_adapters import (
    MockWalletProviderAdapter,
    WalletProviderPayloadError,
)
from core.services.wallet_provider_service import (
    WalletProviderSignatureError,
    build_mock_provider_signature,
)


class MockWalletProviderAdapterTests(SimpleTestCase):

    secret = "adapter-test-secret"

    def adapter(self):
        return MockWalletProviderAdapter(
            secret=self.secret
        )

    def signed_headers(self, body):
        signature = build_mock_provider_signature(
            body,
            self.secret,
        )

        return {
            "X-DJINA-Signature": signature,
        }

    def test_valid_signed_json_is_parsed(self):
        body = json.dumps(
            {
                "topup_id": 12,
                "status": "success",
            },
            separators=(",", ":"),
        ).encode("utf-8")

        payload = self.adapter().verify_and_parse(
            raw_body=body,
            headers=self.signed_headers(body),
        )

        self.assertEqual(
            payload["topup_id"],
            12,
        )

        self.assertEqual(
            payload["status"],
            "success",
        )

    def test_missing_signature_rejected(self):
        body = b'{"topup_id":12}'

        with self.assertRaises(
            WalletProviderSignatureError
        ):
            self.adapter().verify_and_parse(
                raw_body=body,
                headers={},
            )

    def test_wrong_signature_rejected(self):
        body = b'{"topup_id":12}'

        with self.assertRaises(
            WalletProviderSignatureError
        ):
            self.adapter().verify_and_parse(
                raw_body=body,
                headers={
                    "X-DJINA-Signature":
                        "not-a-valid-signature"
                },
            )

    def test_signature_is_bound_to_exact_body(self):
        original = b'{"topup_id":12}'

        headers = self.signed_headers(
            original
        )

        modified = b'{"topup_id":13}'

        with self.assertRaises(
            WalletProviderSignatureError
        ):
            self.adapter().verify_and_parse(
                raw_body=modified,
                headers=headers,
            )

    def test_invalid_json_rejected_after_valid_signature(self):
        body = b'not-json'

        with self.assertRaises(
            WalletProviderPayloadError
        ):
            self.adapter().verify_and_parse(
                raw_body=body,
                headers=self.signed_headers(body),
            )

    def test_invalid_utf8_rejected(self):
        body = b"\xff\xfe"

        with self.assertRaises(
            WalletProviderPayloadError
        ):
            self.adapter().verify_and_parse(
                raw_body=body,
                headers=self.signed_headers(body),
            )

    def test_json_array_rejected(self):
        body = b'["unexpected"]'

        with self.assertRaises(
            WalletProviderPayloadError
        ):
            self.adapter().verify_and_parse(
                raw_body=body,
                headers=self.signed_headers(body),
            )

    def test_empty_json_object_is_valid_transport_payload(self):
        body = b"{}"

        payload = self.adapter().verify_and_parse(
            raw_body=body,
            headers=self.signed_headers(body),
        )

        self.assertEqual(payload, {})
