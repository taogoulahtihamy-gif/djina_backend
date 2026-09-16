from django.db import IntegrityError, transaction

from core.models import DriverWallet, WalletTopUp
from core.services.wallet_service import (
    WalletError,
    WalletNotActiveError,
    _normalize_amount,
)


class WalletTopUpError(WalletError):
    pass


class WalletTopUpConflictError(WalletTopUpError):
    pass


def _normalize_text(value, *, field, max_length):
    if not isinstance(value, str):
        raise WalletTopUpError(f"Invalid {field}.")

    value = value.strip()

    if not value or len(value) > max_length:
        raise WalletTopUpError(f"Invalid {field}.")

    return value


def _existing_topup(
    existing,
    *,
    wallet_id,
    amount,
    provider,
    phone,
    idempotency_key,
):
    if (
        existing.wallet_id != wallet_id
        or existing.amount != amount
        or existing.provider != provider
        or existing.phone != phone
        or existing.idempotency_key != idempotency_key
    ):
        raise WalletTopUpConflictError(
            "Idempotency key already belongs to another top-up request."
        )

    return existing


def request_wallet_topup(
    *,
    wallet,
    amount,
    provider,
    phone,
    idempotency_key,
):
    """Crée une demande PENDING sans jamais créditer le wallet."""

    amount = _normalize_amount(amount)

    phone = _normalize_text(
        phone,
        field="phone",
        max_length=20,
    )

    idempotency_key = _normalize_text(
        idempotency_key,
        field="idempotency key",
        max_length=120,
    )

    if provider not in WalletTopUp.Provider.values:
        raise WalletTopUpError("Invalid provider.")

    if wallet.pk is None or wallet._state.adding:
        raise WalletTopUpError("Wallet must be saved.")

    try:
        with transaction.atomic():
            locked_wallet = (
                DriverWallet.objects
                .select_for_update()
                .get(pk=wallet.pk)
            )

            existing = WalletTopUp.objects.filter(
                idempotency_key=idempotency_key
            ).first()

            # Retry historique autorisé même si le wallet a ensuite
            # été bloqué ou fermé.
            if existing is not None:
                return (
                    _existing_topup(
                        existing,
                        wallet_id=locked_wallet.pk,
                        amount=amount,
                        provider=provider,
                        phone=phone,
                        idempotency_key=idempotency_key,
                    ),
                    False,
                )

            if locked_wallet.status != DriverWallet.Status.ACTIVE:
                raise WalletNotActiveError(
                    "Wallet must be active."
                )

            topup = WalletTopUp.objects.create(
                wallet=locked_wallet,
                amount=amount,
                currency="XAF",
                provider=provider,
                phone=phone,
                provider_reference=None,
                idempotency_key=idempotency_key,
                status=WalletTopUp.Status.PENDING,
                confirmed_at=None,
                failure_reason=None,
            )

            return topup, True

    except IntegrityError:
        # Collision globale d'idempotence.
        existing = WalletTopUp.objects.filter(
            idempotency_key=idempotency_key
        ).first()

        if existing is None:
            raise

        return (
            _existing_topup(
                existing,
                wallet_id=wallet.pk,
                amount=amount,
                provider=provider,
                phone=phone,
                idempotency_key=idempotency_key,
            ),
            False,
        )
