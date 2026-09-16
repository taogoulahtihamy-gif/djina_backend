from django.shortcuts import get_object_or_404

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.models import Driver, WalletTransaction
from core.views import IsDriverUser
from core.serializers_wallet import (
    DriverWalletSerializer,
    WalletTransactionSerializer,
)
from core.services.wallet_service import get_or_create_driver_wallet


class DriverWalletViewSet(viewsets.GenericViewSet):
    """API en lecture du portefeuille du chauffeur authentifié."""

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

        serializer = DriverWalletSerializer(
            wallet,
            context=self.get_serializer_context(),
        )

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
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
                context=self.get_serializer_context(),
            )
            return self.get_paginated_response(
                serializer.data
            )

        serializer = WalletTransactionSerializer(
            queryset,
            many=True,
            context=self.get_serializer_context(),
        )

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )
