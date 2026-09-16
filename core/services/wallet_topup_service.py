from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import (
    DriverWallet,
    WalletTopUp,
    WalletTransaction,
)
from core.services.wallet_service import (
    WalletError,
    WalletNotActiveError,
    _normalize_amount,
    credit_wallet,
)


class WalletTopUpError(WalletError):
    pass


class WalletTopUpConflictError(WalletTopUpError):
    pass


class WalletTopUpStateError(WalletTopUpError):
    pass


class WalletTopUpConsistencyError(WalletTopUpError):
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
    """Crée une demande PENDING sans créditer le wallet."""

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


def _confirmation_transaction_key(topup):
    return f"topup-confirm:{topup.pk}"


def _validate_confirmation_identity(
    topup,
    *,
    provider,
    provider_reference,
    confirmed_amount,
):
    if topup.provider != provider:
        raise WalletTopUpConflictError(
            "Provider does not match top-up request."
        )

    if topup.amount != confirmed_amount:
        raise WalletTopUpConflictError(
            "Confirmed amount does not match top-up request."
        )

    if (
        topup.provider_reference is not None
        and topup.provider_reference != provider_reference
    ):
        raise WalletTopUpConflictError(
            "Provider reference does not match confirmed top-up."
        )


def _normalize_callback_provider_status(
    provider_status,
    *,
    fallback,
):
    if provider_status in (None, ""):
        provider_status = fallback

    return _normalize_text(
        provider_status,
        field="provider status",
        max_length=50,
    )


@transaction.atomic
def confirm_wallet_topup(
    *,
    topup_id,
    provider,
    provider_reference,
    confirmed_amount,
    provider_status=None,
):
    """Confirme côté serveur une recharge réellement payée.

    Ordre des verrous :
        DriverWallet -> WalletTopUp

    Le montant stocké dans WalletTopUp reste l'autorité financière.
    confirmed_amount sert uniquement à vérifier le callback fournisseur.

    Un callback répété à l'identique est idempotent.
    """

    provider_reference = _normalize_text(
        provider_reference,
        field="provider reference",
        max_length=120,
    )

    confirmed_amount = _normalize_amount(
        confirmed_amount
    )

    provider_status_was_supplied = (
        provider_status not in (None, "")
    )

    provider_status = (
        _normalize_callback_provider_status(
            provider_status,
            fallback=WalletTopUp.Status.SUCCESS,
        )
    )

    if provider not in WalletTopUp.Provider.values:
        raise WalletTopUpError("Invalid provider.")

    try:
        wallet_id = (
            WalletTopUp.objects
            .values_list("wallet_id", flat=True)
            .get(pk=topup_id)
        )
    except WalletTopUp.DoesNotExist as exc:
        raise WalletTopUpError(
            "Top-up request does not exist."
        ) from exc

    locked_wallet = (
        DriverWallet.objects
        .select_for_update()
        .get(pk=wallet_id)
    )

    locked_topup = (
        WalletTopUp.objects
        .select_for_update()
        .get(pk=topup_id)
    )

    _validate_confirmation_identity(
        locked_topup,
        provider=provider,
        provider_reference=provider_reference,
        confirmed_amount=confirmed_amount,
    )

    transaction_key = _confirmation_transaction_key(
        locked_topup
    )

    if locked_topup.status == WalletTopUp.Status.SUCCESS:
        existing_transaction = WalletTransaction.objects.filter(
            idempotency_key=transaction_key
        ).first()

        if existing_transaction is None:
            raise WalletTopUpConsistencyError(
                "Successful top-up has no wallet transaction."
            )

        stored_provider_status = (
            locked_topup.provider_status or ""
        ).strip()

        if stored_provider_status:
            if (
                provider_status_was_supplied
                and stored_provider_status
                != provider_status
            ):
                raise WalletTopUpConflictError(
                    "Provider status does not match existing successful top-up."
                )
        else:
            # Compatibilité avec les anciens top-ups SUCCESS
            # créés avant la persistance de provider_status.
            locked_topup.provider_status = (
                provider_status
            )
            locked_topup.save(
                update_fields=[
                    "provider_status",
                    "updated_at",
                ]
            )

        wallet_transaction = credit_wallet(
            wallet=locked_wallet,
            amount=locked_topup.amount,
            transaction_type=WalletTransaction.Type.TOPUP,
            idempotency_key=transaction_key,
            provider=locked_topup.provider,
            provider_reference=provider_reference,
            metadata={"topup_id": locked_topup.pk},
        )

        return locked_topup, wallet_transaction, False

    if locked_topup.status in (
        WalletTopUp.Status.FAILED,
        WalletTopUp.Status.CANCELLED,
    ):
        raise WalletTopUpStateError(
            "Top-up request cannot be confirmed."
        )

    if locked_topup.status != WalletTopUp.Status.PENDING:
        raise WalletTopUpStateError(
            "Top-up request is not pending."
        )

    conflict = (
        WalletTopUp.objects
        .filter(
            provider=provider,
            provider_reference=provider_reference,
        )
        .exclude(pk=locked_topup.pk)
        .exists()
    )

    if conflict:
        raise WalletTopUpConflictError(
            "Provider reference is already used."
        )

    if locked_wallet.status != DriverWallet.Status.ACTIVE:
        raise WalletNotActiveError(
            "Wallet must be active."
        )

    wallet_transaction = credit_wallet(
        wallet=locked_wallet,
        amount=locked_topup.amount,
        transaction_type=WalletTransaction.Type.TOPUP,
        idempotency_key=transaction_key,
        provider=locked_topup.provider,
        provider_reference=provider_reference,
        metadata={"topup_id": locked_topup.pk},
    )

    locked_topup.provider_reference = provider_reference
    locked_topup.provider_status = provider_status
    locked_topup.status = WalletTopUp.Status.SUCCESS
    locked_topup.confirmed_at = timezone.now()
    locked_topup.failure_reason = None

    try:
        locked_topup.save(
            update_fields=[
                "provider_reference",
                "provider_status",
                "status",
                "confirmed_at",
                "failure_reason",
                "updated_at",
            ]
        )
    except IntegrityError as exc:
        duplicate = (
            WalletTopUp.objects
            .filter(
                provider=provider,
                provider_reference=provider_reference,
            )
            .exclude(pk=locked_topup.pk)
            .exists()
        )

        if duplicate:
            raise WalletTopUpConflictError(
                "Provider reference is already used."
            ) from exc

        raise

    return locked_topup, wallet_transaction, True


def _normalize_failure_reason(value):
    return _normalize_text(
        value,
        field="failure reason",
        max_length=1000,
    )


@transaction.atomic
def fail_wallet_topup(
    *,
    topup_id,
    provider,
    provider_reference,
    amount,
    failure_reason,
    provider_status=None,
):
    """Enregistre un échec fournisseur sans aucun mouvement financier.

    Ordre des verrous :
        DriverWallet -> WalletTopUp

    Un callback FAILED répété à l'identique est idempotent.
    """

    provider_reference = _normalize_text(
        provider_reference,
        field="provider reference",
        max_length=120,
    )

    amount = _normalize_amount(amount)

    provider_status_was_supplied = (
        provider_status not in (None, "")
    )

    provider_status = (
        _normalize_callback_provider_status(
            provider_status,
            fallback=WalletTopUp.Status.FAILED,
        )
    )

    failure_reason = _normalize_failure_reason(
        failure_reason
    )

    if provider not in WalletTopUp.Provider.values:
        raise WalletTopUpError("Invalid provider.")

    try:
        wallet_id = (
            WalletTopUp.objects
            .values_list("wallet_id", flat=True)
            .get(pk=topup_id)
        )
    except WalletTopUp.DoesNotExist as exc:
        raise WalletTopUpError(
            "Top-up request does not exist."
        ) from exc

    DriverWallet.objects.select_for_update().get(
        pk=wallet_id
    )

    locked_topup = (
        WalletTopUp.objects
        .select_for_update()
        .get(pk=topup_id)
    )

    _validate_confirmation_identity(
        locked_topup,
        provider=provider,
        provider_reference=provider_reference,
        confirmed_amount=amount,
    )

    if locked_topup.status == WalletTopUp.Status.SUCCESS:
        raise WalletTopUpStateError(
            "Successful top-up cannot be marked failed."
        )

    if locked_topup.status == WalletTopUp.Status.CANCELLED:
        raise WalletTopUpStateError(
            "Cancelled top-up cannot be marked failed."
        )

    if locked_topup.status == WalletTopUp.Status.FAILED:
        if locked_topup.failure_reason != failure_reason:
            raise WalletTopUpConflictError(
                "Failure callback does not match existing history."
            )

        stored_provider_status = (
            locked_topup.provider_status or ""
        ).strip()

        if stored_provider_status:
            if (
                provider_status_was_supplied
                and stored_provider_status
                != provider_status
            ):
                raise WalletTopUpConflictError(
                    "Provider status does not match existing failed top-up."
                )
        else:
            # Compatibilité avec les anciens top-ups FAILED.
            locked_topup.provider_status = (
                provider_status
            )
            locked_topup.save(
                update_fields=[
                    "provider_status",
                    "updated_at",
                ]
            )

        return locked_topup, False

    if locked_topup.status != WalletTopUp.Status.PENDING:
        raise WalletTopUpStateError(
            "Top-up request is not pending."
        )

    duplicate = (
        WalletTopUp.objects
        .filter(
            provider=provider,
            provider_reference=provider_reference,
        )
        .exclude(pk=locked_topup.pk)
        .exists()
    )

    if duplicate:
        raise WalletTopUpConflictError(
            "Provider reference is already used."
        )

    locked_topup.provider_reference = provider_reference
    locked_topup.provider_status = provider_status
    locked_topup.status = WalletTopUp.Status.FAILED
    locked_topup.failure_reason = failure_reason
    locked_topup.confirmed_at = None

    try:
        locked_topup.save(
            update_fields=[
                "provider_reference",
                "provider_status",
                "status",
                "failure_reason",
                "confirmed_at",
                "updated_at",
            ]
        )
    except IntegrityError as exc:
        duplicate = (
            WalletTopUp.objects
            .filter(
                provider=provider,
                provider_reference=provider_reference,
            )
            .exclude(pk=locked_topup.pk)
            .exists()
        )

        if duplicate:
            raise WalletTopUpConflictError(
                "Provider reference is already used."
            ) from exc

        raise

    return locked_topup, True
