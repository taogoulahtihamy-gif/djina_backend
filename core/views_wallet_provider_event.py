from rest_framework.exceptions import (
    ValidationError,
)
from rest_framework.permissions import (
    BasePermission,
    IsAuthenticated,
)
from rest_framework.viewsets import (
    ReadOnlyModelViewSet,
)

from core.models import (
    WalletProviderEvent,
    WalletTopUp,
)
from core.serializers_wallet_provider_event import (
    WalletProviderEventAdminSerializer,
)


class IsWalletProviderEventAdmin(
    BasePermission
):
    message = (
        "Administrative access is required."
    )

    def has_permission(
        self,
        request,
        view,
    ):
        user = request.user

        if (
            not user
            or not user.is_authenticated
        ):
            return False

        if (
            getattr(
                user,
                "is_superuser",
                False,
            )
            or getattr(
                user,
                "is_staff",
                False,
            )
        ):
            return True

        return (
            getattr(
                user,
                "user_type",
                None,
            )
            in {
                "admin",
                "super_admin",
            }
        )


class WalletProviderEventAdminViewSet(
    ReadOnlyModelViewSet
):
    """Journal fournisseur visible uniquement en admin.

    Aucune création, modification ou suppression
    n'est exposée par cette API.
    """

    serializer_class = (
        WalletProviderEventAdminSerializer
    )

    permission_classes = [
        IsAuthenticated,
        IsWalletProviderEventAdmin,
    ]

    http_method_names = [
        "get",
        "head",
        "options",
    ]

    queryset = (
        WalletProviderEvent.objects
        .select_related(
            "topup",
            "wallet_transaction",
        )
        .all()
    )

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .order_by(
                "-created_at",
                "-pk",
            )
        )

        params = (
            self.request.query_params
        )

        provider = params.get(
            "provider"
        )

        if provider:
            provider = provider.strip()

            if (
                provider
                not in
                WalletTopUp.Provider.values
            ):
                raise ValidationError(
                    {
                        "provider":
                            "Invalid provider."
                    }
                )

            queryset = queryset.filter(
                provider=provider
            )

        outcome = params.get(
            "outcome"
        )

        if outcome:
            outcome = outcome.strip()

            if (
                outcome
                not in
                WalletProviderEvent
                .Outcome
                .values
            ):
                raise ValidationError(
                    {
                        "outcome":
                            "Invalid outcome."
                    }
                )

            queryset = queryset.filter(
                outcome=outcome
            )

        topup = params.get(
            "topup"
        )

        if topup:
            try:
                topup_id = int(topup)
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise ValidationError(
                    {
                        "topup":
                            "Invalid top-up id."
                    }
                ) from exc

            if topup_id <= 0:
                raise ValidationError(
                    {
                        "topup":
                            "Invalid top-up id."
                    }
                )

            # reported_topup_id permet aussi
            # de retrouver un callback reçu pour
            # un TopUp inexistant/localement supprimé.
            queryset = queryset.filter(
                reported_topup_id=
                    topup_id
            )

        provider_reference = (
            params.get(
                "provider_reference"
            )
        )

        if provider_reference:
            provider_reference = (
                provider_reference.strip()
            )

            if (
                not provider_reference
                or len(
                    provider_reference
                ) > 120
                or "\r"
                in provider_reference
                or "\n"
                in provider_reference
            ):
                raise ValidationError(
                    {
                        "provider_reference":
                            "Invalid provider reference."
                    }
                )

            queryset = queryset.filter(
                provider_reference=
                    provider_reference
            )

        return queryset
