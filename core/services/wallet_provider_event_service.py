from django.utils import timezone

from core.models import (
    WalletProviderEvent,
    WalletTopUp,
)
from core.services.wallet_provider_callback_service import (
    process_wallet_provider_callback,
)


def _safe_error_message(exc):
    message = str(exc)

    if len(message) > 1000:
        message = message[:1000]

    return message


def process_journaled_wallet_provider_callback(
    *,
    topup_id,
    provider,
    provider_reference,
    amount,
    callback_status,
    failure_reason=None,
    provider_status=None,
):
    """Journalise puis traite un callback fournisseur.

    Le journal technique est distinct du ledger financier.

    Aucun payload brut, header HTTP, token ou secret
    d'authentification n'est stocké ici.
    """

    topup = (
        WalletTopUp.objects
        .filter(pk=topup_id)
        .first()
    )

    event = WalletProviderEvent.objects.create(
        topup=topup,
        reported_topup_id=topup_id,
        provider=provider,
        provider_reference=provider_reference,
        callback_status=callback_status,
        provider_status=(
            provider_status or ""
        ),
        amount=amount,
        failure_reason=(
            failure_reason or ""
        ),
        outcome=(
            WalletProviderEvent
            .Outcome
            .RECEIVED
        ),
    )

    try:
        result = process_wallet_provider_callback(
            topup_id=topup_id,
            provider=provider,
            provider_reference=
                provider_reference,
            amount=amount,
            callback_status=
                callback_status,
            failure_reason=
                failure_reason,
            provider_status=
                provider_status,
        )

    except Exception as exc:
        event.outcome = (
            WalletProviderEvent
            .Outcome
            .REJECTED
        )

        event.processed = False

        event.error_type = (
            exc.__class__.__name__[:100]
        )

        event.error_message = (
            _safe_error_message(exc)
        )

        event.completed_at = timezone.now()

        event.save(
            update_fields=[
                "outcome",
                "processed",
                "error_type",
                "error_message",
                "completed_at",
                "updated_at",
            ]
        )

        raise

    event.outcome = (
        WalletProviderEvent
        .Outcome
        .ACCEPTED
    )

    event.processed = (
        result.processed
    )

    event.wallet_transaction = (
        result.wallet_transaction
    )

    event.completed_at = timezone.now()

    event.save(
        update_fields=[
            "outcome",
            "processed",
            "wallet_transaction",
            "completed_at",
            "updated_at",
        ]
    )

    return result
