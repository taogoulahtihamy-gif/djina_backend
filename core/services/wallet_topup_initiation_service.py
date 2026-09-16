from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import WalletTopUp
from core.services.wallet_provider_outbound import (
    WalletProviderOutboundError,
)
from core.services.wallet_provider_registry import (
    WalletProviderRegistryError,
)
from core.services.wallet_provider_runtime import (
    WalletProviderRuntimeError,
    build_wallet_provider_runtime,
)


class WalletTopUpInitiationError(Exception):
    pass


class WalletTopUpInitiationStateError(
    WalletTopUpInitiationError
):
    pass


class WalletTopUpInitiationProviderError(
    WalletTopUpInitiationError
):
    pass


class WalletTopUpInitiationConflictError(
    WalletTopUpInitiationError
):
    pass


class WalletTopUpInitiationConsistencyError(
    WalletTopUpInitiationError
):
    pass


@dataclass(frozen=True)
class WalletTopUpInitiationResult:
    topup: WalletTopUp
    provider_reference: str
    provider_status: str | None
    initiated: bool


def _provider_idempotency_key(topup):
    return f"topup:{topup.pk}"


def _provider_correlation_id(topup):
    return f"djina-topup:{topup.pk}"


def _validate_pending_topup(topup):
    if topup.status != WalletTopUp.Status.PENDING:
        raise WalletTopUpInitiationStateError(
            "Only pending top-ups can be initiated."
        )

    if topup.provider not in {
        WalletTopUp.Provider.AIRTEL_MONEY,
        WalletTopUp.Provider.MOOV_MONEY,
    }:
        raise WalletTopUpInitiationProviderError(
            "Top-up provider is not supported for remote initiation."
        )


def _normalize_provider_reference(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 120
        or "\r" in value
        or "\n" in value
    ):
        raise WalletTopUpInitiationConsistencyError(
            "Provider returned an invalid reference."
        )

    return value.strip()


def _normalize_provider_status(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 50
        or "\r" in value
        or "\n" in value
    ):
        raise WalletTopUpInitiationConsistencyError(
            "Provider returned an invalid status."
        )

    return value.strip()


def _existing_result(topup):
    reference = (
        topup.provider_reference or ""
    ).strip()

    if not reference:
        return None

    provider_status = (
        (topup.provider_status or "").strip()
        or None
    )

    return WalletTopUpInitiationResult(
        topup=topup,
        provider_reference=reference,
        provider_status=provider_status,
        initiated=False,
    )


def initiate_wallet_topup(
    *,
    topup_id,
    runtime_builder=build_wallet_provider_runtime,
):
    """Initie un top-up PENDING auprès du fournisseur.

    Aucune écriture financière n'a lieu ici.

    provider_reference, provider_status et initiated_at
    sont uniquement des métadonnées d'initiation.
    """

    if (
        isinstance(topup_id, bool)
        or not isinstance(topup_id, int)
        or topup_id <= 0
    ):
        raise WalletTopUpInitiationError(
            "Invalid top-up id."
        )

    try:
        topup = WalletTopUp.objects.get(
            pk=topup_id
        )
    except WalletTopUp.DoesNotExist as exc:
        raise WalletTopUpInitiationError(
            "Top-up request does not exist."
        ) from exc

    _validate_pending_topup(topup)

    existing = _existing_result(topup)

    if existing is not None:
        return existing

    try:
        runtime = runtime_builder(
            topup.provider
        )
    except (
        WalletProviderRegistryError,
        WalletProviderRuntimeError,
    ) as exc:
        raise WalletTopUpInitiationProviderError(
            "Wallet provider runtime is unavailable."
        ) from exc

    try:
        remote_result = (
            runtime.outbound_service
            .initiate_topup(
                topup_id=topup.pk,
                amount=topup.amount,
                currency=topup.currency,
                phone=topup.phone,
                idempotency_key=
                    _provider_idempotency_key(
                        topup
                    ),
                correlation_id=
                    _provider_correlation_id(
                        topup
                    ),
            )
        )
    except WalletProviderOutboundError as exc:
        raise WalletTopUpInitiationProviderError(
            "Provider top-up initiation failed."
        ) from exc

    provider_reference = (
        _normalize_provider_reference(
            remote_result.provider_reference
        )
    )

    provider_status = (
        _normalize_provider_status(
            remote_result.provider_status
        )
    )

    try:
        with transaction.atomic():
            locked = (
                WalletTopUp.objects
                .select_for_update()
                .get(pk=topup.pk)
            )

            # Le callback peut gagner la course pendant
            # que l'appel réseau est en vol.
            if locked.status != WalletTopUp.Status.PENDING:
                if (
                    locked.provider_reference
                    == provider_reference
                ):
                    return WalletTopUpInitiationResult(
                        topup=locked,
                        provider_reference=
                            provider_reference,
                        provider_status=(
                            (
                                locked.provider_status
                                or ""
                            ).strip()
                            or provider_status
                        ),
                        initiated=False,
                    )

                raise (
                    WalletTopUpInitiationConsistencyError(
                        "Top-up state changed during provider initiation."
                    )
                )

            # Un autre worker peut avoir déjà persisté
            # le résultat de la même initiation idempotente.
            if locked.provider_reference:
                if (
                    locked.provider_reference
                    == provider_reference
                ):
                    return WalletTopUpInitiationResult(
                        topup=locked,
                        provider_reference=
                            provider_reference,
                        provider_status=(
                            (
                                locked.provider_status
                                or ""
                            ).strip()
                            or provider_status
                        ),
                        initiated=False,
                    )

                raise (
                    WalletTopUpInitiationConsistencyError(
                        "Provider returned conflicting references for the same top-up."
                    )
                )

            duplicate = (
                WalletTopUp.objects
                .filter(
                    provider=locked.provider,
                    provider_reference=
                        provider_reference,
                )
                .exclude(pk=locked.pk)
                .exists()
            )

            if duplicate:
                raise WalletTopUpInitiationConflictError(
                    "Provider reference is already used by another top-up."
                )

            locked.provider_reference = (
                provider_reference
            )
            locked.provider_status = (
                provider_status
            )
            locked.initiated_at = timezone.now()

            locked.save(
                update_fields=[
                    "provider_reference",
                    "provider_status",
                    "initiated_at",
                    "updated_at",
                ]
            )

            return WalletTopUpInitiationResult(
                topup=locked,
                provider_reference=
                    provider_reference,
                provider_status=
                    provider_status,
                initiated=True,
            )

    except IntegrityError as exc:
        duplicate = (
            WalletTopUp.objects
            .filter(
                provider=topup.provider,
                provider_reference=
                    provider_reference,
            )
            .exclude(pk=topup.pk)
            .exists()
        )

        if duplicate:
            raise WalletTopUpInitiationConflictError(
                "Provider reference is already used by another top-up."
            ) from exc

        raise
