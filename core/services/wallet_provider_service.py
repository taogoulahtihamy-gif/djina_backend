import hashlib
import hmac


class WalletProviderSignatureError(Exception):
    pass


def build_mock_provider_signature(raw_body, secret):
    if not isinstance(raw_body, bytes):
        raise WalletProviderSignatureError(
            "Webhook body must be bytes."
        )

    if isinstance(secret, str):
        secret = secret.encode("utf-8")

    if not isinstance(secret, bytes) or not secret:
        raise WalletProviderSignatureError(
            "Provider secret is not configured."
        )

    return hmac.new(
        secret,
        raw_body,
        hashlib.sha256,
    ).hexdigest()


def verify_mock_provider_signature(
    *,
    raw_body,
    signature,
    secret,
):
    if not isinstance(signature, str):
        raise WalletProviderSignatureError(
            "Missing provider signature."
        )

    signature = signature.strip().lower()

    if not signature:
        raise WalletProviderSignatureError(
            "Missing provider signature."
        )

    expected = build_mock_provider_signature(
        raw_body,
        secret,
    )

    if not hmac.compare_digest(
        signature,
        expected,
    ):
        raise WalletProviderSignatureError(
            "Invalid provider signature."
        )

    return True
