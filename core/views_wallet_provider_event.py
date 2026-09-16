from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import (
    parse_date,
    parse_datetime,
)

from rest_framework.exceptions import (
    ValidationError,
)
from rest_framework.pagination import (
    PageNumberPagination,
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


class WalletProviderEventPagination(
    PageNumberPagination
):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


def _parse_boolean_filter(
    value,
    *,
    field,
):
    normalized = (
        str(value)
        .strip()
        .lower()
    )

    if normalized in {
        "true",
        "1",
    }:
        return True

    if normalized in {
        "false",
        "0",
    }:
        return False

    raise ValidationError(
        {
            field:
                "Expected true, false, 1 or 0."
        }
    )


def _parse_datetime_filter(
    value,
    *,
    field,
    end_of_day=False,
):
    value = str(value).strip()

    parsed = parse_datetime(
        value
    )

    if parsed is None:
        parsed_date = parse_date(
            value
        )

        if parsed_date is None:
            raise ValidationError(
                {
                    field:
                        "Expected an ISO 8601 date or datetime."
                }
            )

        if end_of_day:
            parsed = datetime.combine(
                parsed_date,
                datetime.max.time(),
            )
        else:
            parsed = datetime.combine(
                parsed_date,
                datetime.min.time(),
            )

    if timezone.is_naive(
        parsed
    ):
        parsed = timezone.make_aware(
            parsed,
            timezone.get_current_timezone(),
        )

    return parsed


class WalletProviderEventAdminViewSet(
    ReadOnlyModelViewSet
):
    """Journal fournisseur visible uniquement en admin.

    API strictement read-only.
    """

    serializer_class = (
        WalletProviderEventAdminSerializer
    )

    permission_classes = [
        IsAuthenticated,
        IsWalletProviderEventAdmin,
    ]

    pagination_class = (
        WalletProviderEventPagination
    )

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

        callback_status = params.get(
            "callback_status"
        )

        if callback_status:
            callback_status = (
                callback_status.strip()
            )

            if callback_status not in {
                WalletTopUp.Status.SUCCESS,
                WalletTopUp.Status.FAILED,
            }:
                raise ValidationError(
                    {
                        "callback_status":
                            "Invalid callback status."
                    }
                )

            queryset = queryset.filter(
                callback_status=
                    callback_status
            )

        processed = params.get(
            "processed"
        )

        if processed is not None:
            processed_value = (
                _parse_boolean_filter(
                    processed,
                    field="processed",
                )
            )

            queryset = queryset.filter(
                processed=processed_value
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

        error_type = params.get(
            "error_type"
        )

        if error_type:
            error_type = (
                error_type.strip()
            )

            if (
                not error_type
                or len(error_type) > 100
                or "\r" in error_type
                or "\n" in error_type
            ):
                raise ValidationError(
                    {
                        "error_type":
                            "Invalid error type."
                    }
                )

            queryset = queryset.filter(
                error_type=error_type
            )

        created_from = params.get(
            "created_from"
        )

        if created_from:
            queryset = queryset.filter(
                created_at__gte=
                    _parse_datetime_filter(
                        created_from,
                        field="created_from",
                    )
            )

        created_to = params.get(
            "created_to"
        )

        if created_to:
            queryset = queryset.filter(
                created_at__lte=
                    _parse_datetime_filter(
                        created_to,
                        field="created_to",
                        end_of_day=True,
                    )
            )

        return queryset
