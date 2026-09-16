from dataclasses import dataclass

from django.db import IntegrityError, transaction

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
    # Clé serveur stable.
    # On ne dépend pas d'une nouvelle valeur générée à chaque retry.
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


def _existing_result(topup):
    reference = (
        topup.provider_reference or ""
    ).strip()

    if not reference:
        return None

    return WalletTopUpInitiationResult(
        topup=topup,
        provider_reference=reference,
        provider_status=None,
        initiated=False,
    )


def initiate_wallet_topup(
    *,
    topup_id,
    runtime_builder=build_wallet_provider_runtime,
):
    """Initie une demande PENDING auprès du fournisseur.

    Aucun solde wallet n'est modifié ici.

    Deux appels peuvent atteindre le fournisseur en concurrence.
    Ils utilisent donc exactement la même clé d'idempotence serveur.
    Le vrai adaptateur fournisseur devra garantir que cette clé est
    transmise selon le protocole officiel correspondant.
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
        remote_result.provider_reference
    )

    if (
        not isinstance(provider_reference, str)
        or not provider_reference.strip()
        or len(provider_reference.strip()) > 120
    ):
        raise WalletTopUpInitiationConsistencyError(
            "Provider returned an invalid reference."
        )

    provider_reference = (
        provider_reference.strip()
    )

    try:
        with transaction.atomic():
            locked = (
                WalletTopUp.objects
                .select_for_update()
                .get(pk=topup.pk)
            )

            # Un callback peut théoriquement être arrivé pendant
            # l'appel réseau. On ne réécrit jamais son état terminal.
            if locked.status != WalletTopUp.Status.PENDING:
                if (
                    locked.provider_reference
                    == provider_reference
                ):
                    return WalletTopUpInitiationResult(
                        topup=locked,
                        provider_reference=
                            provider_reference,
                        provider_status=
                            remote_result.provider_status,
                        initiated=False,
                    )

                raise (
                    WalletTopUpInitiationConsistencyError(
                        "Top-up state changed during provider initiation."
                    )
                )

            if locked.provider_reference:
                if (
                    locked.provider_reference
                    == provider_reference
                ):
                    return WalletTopUpInitiationResult(
                        topup=locked,
                        provider_reference=
                            provider_reference,
                        provider_status=
                            remote_result.provider_status,
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

            locked.save(
                update_fields=[
                    "provider_reference",
                    "updated_at",
                ]
            )

            return WalletTopUpInitiationResult(
                topup=locked,
                provider_reference=
                    provider_reference,
                provider_status=
                    remote_result.provider_status,
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
