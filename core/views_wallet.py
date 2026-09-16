from django.shortcuts import get_object_or_404

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.models import Driver, WalletTopUp, WalletTransaction
from core.serializers_wallet import (
    DriverWalletSerializer,
    WalletTopUpCreateSerializer,
    WalletTopUpSerializer,
    WalletTransactionSerializer,
)
from core.services.wallet_service import (
    WalletError,
    WalletNotActiveError,
    get_or_create_driver_wallet,
)
from core.services.wallet_topup_service import (
    WalletTopUpConflictError,
    request_wallet_topup,
)
from core.views import IsDriverUser


class DriverWalletViewSet(viewsets.GenericViewSet):
    permission_classes = [IsDriverUser]
    serializer_class = DriverWalletSerializer

    def _driver(self):
        return get_object_or_404(
            Driver,
            user=self.request.user,
            deleted_at__isnull=True,
        )

    def _wallet(self):
        return get_or_create_driver_wallet(
            self._driver()
        )

    def list(self, request):
        wallet = self._wallet()

        return Response(
            DriverWalletSerializer(
                wallet,
                context=self.get_serializer_context(),
            ).data
        )

    @action(
        detail=False,
        methods=["get"],
        url_path="transactions",
    )
    def transactions(self, request):
        wallet = self._wallet()

        queryset = (
            WalletTransaction.objects
            .filter(wallet=wallet)
            .select_related("course", "commission")
            .order_by("-created_at", "-pk")
        )

        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = WalletTransactionSerializer(
                page,
                many=True,
            )
            return self.get_paginated_response(
                serializer.data
            )

        return Response(
            WalletTransactionSerializer(
                queryset,
                many=True,
            ).data
        )

    @action(
        detail=False,
        methods=["get", "post"],
        url_path="topups",
    )
    def topups(self, request):
        wallet = self._wallet()

        if request.method == "GET":
            queryset = (
                WalletTopUp.objects
                .filter(wallet=wallet)
                .order_by("-requested_at", "-pk")
            )

            page = self.paginate_queryset(queryset)

            if page is not None:
                serializer = WalletTopUpSerializer(
                    page,
                    many=True,
                )
                return self.get_paginated_response(
                    serializer.data
                )

            return Response(
                WalletTopUpSerializer(
                    queryset,
                    many=True,
                ).data
            )

        payload = WalletTopUpCreateSerializer(
            data=request.data
        )
        payload.is_valid(raise_exception=True)

        try:
            topup, created = request_wallet_topup(
                wallet=wallet,
                **payload.validated_data,
            )
        except WalletTopUpConflictError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except WalletNotActiveError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except WalletError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            WalletTopUpSerializer(topup).data,
            status=(
                status.HTTP_201_CREATED
                if created
                else status.HTTP_200_OK
            ),
        )
