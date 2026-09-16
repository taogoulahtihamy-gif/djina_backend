from dataclasses import dataclass

from core.models import WalletTopUp
from core.services.wallet_topup_service import (
    WalletTopUpError,
    confirm_wallet_topup,
    fail_wallet_topup,
)


class WalletProviderCallbackError(WalletTopUpError):
    pass


@dataclass(frozen=True)
class WalletProviderCallbackResult:
    topup: object
    wallet_transaction: object | None
    processed: bool


def process_wallet_provider_callback(
    *,
    topup_id,
    provider,
    provider_reference,
    amount,
    callback_status,
    failure_reason=None,
    provider_status=None,
):
    """Traite un événement fournisseur déjà authentifié et validé.

    Cette couche ne connaît ni HTTP ni HMAC.
    Elle orchestre uniquement le moteur financier commun.
    """

    if callback_status == WalletTopUp.Status.SUCCESS:
        if failure_reason not in (None, ""):
            raise WalletProviderCallbackError(
                "Successful callback cannot contain a failure reason."
            )

        topup, wallet_transaction, processed = (
            confirm_wallet_topup(
                topup_id=topup_id,
                provider=provider,
                provider_reference=provider_reference,
                confirmed_amount=amount,
                provider_status=provider_status,
            )
        )

        return WalletProviderCallbackResult(
            topup=topup,
            wallet_transaction=wallet_transaction,
            processed=processed,
        )

    if callback_status == WalletTopUp.Status.FAILED:
        if not failure_reason:
            raise WalletProviderCallbackError(
                "Failed callback requires a failure reason."
            )

        topup, processed = fail_wallet_topup(
            topup_id=topup_id,
            provider=provider,
            provider_reference=provider_reference,
            amount=amount,
            failure_reason=failure_reason,
            provider_status=provider_status,
        )

        return WalletProviderCallbackResult(
            topup=topup,
            wallet_transaction=None,
            processed=processed,
        )

    raise WalletProviderCallbackError(
        "Unsupported provider callback status."
    )
