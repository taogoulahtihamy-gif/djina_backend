from django.conf import settings
from django.http import Http404

from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import WalletTopUp
from core.serializers_wallet import WalletTopUpSerializer
from core.services.wallet_provider_adapters import (
    MockWalletProviderAdapter,
    WalletProviderPayloadError,
)
from core.services.wallet_provider_service import (
    WalletProviderSignatureError,
)
from core.services.wallet_service import (
    WalletError,
    WalletNotActiveError,
)
from core.services.wallet_topup_service import (
    WalletTopUpConflictError,
    WalletTopUpError,
    WalletTopUpStateError,
    confirm_wallet_topup,
    fail_wallet_topup,
)


class MockTopUpWebhookSerializer(serializers.Serializer):
    topup_id = serializers.IntegerField(
        min_value=1
    )

    provider = serializers.ChoiceField(
        choices=WalletTopUp.Provider.choices
    )

    provider_reference = serializers.CharField(
        max_length=120,
        trim_whitespace=True,
    )

    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )

    status = serializers.ChoiceField(
        choices=(
            WalletTopUp.Status.SUCCESS,
            WalletTopUp.Status.FAILED,
        )
    )

    failure_reason = serializers.CharField(
        max_length=1000,
        required=False,
        allow_blank=False,
        trim_whitespace=True,
    )

    def validate(self, attrs):
        callback_status = attrs["status"]

        if (
            callback_status == WalletTopUp.Status.FAILED
            and not attrs.get("failure_reason")
        ):
            raise serializers.ValidationError(
                {
                    "failure_reason":
                        "This field is required for a failed callback."
                }
            )

        if (
            callback_status == WalletTopUp.Status.SUCCESS
            and "failure_reason" in attrs
        ):
            raise serializers.ValidationError(
                {
                    "failure_reason":
                        "This field is not allowed for a successful callback."
                }
            )

        return attrs


class MockWalletTopUpWebhookView(APIView):
    """Webhook de développement uniquement.

    Désactivé par défaut.
    Ne représente pas encore le protocole réel Airtel/Moov.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        if not getattr(
            settings,
            "WALLET_MOCK_PROVIDER_ENABLED",
            False,
        ):
            raise Http404

        secret = getattr(
            settings,
            "WALLET_MOCK_PROVIDER_SECRET",
            "",
        )

        raw_body = request.body

        adapter = MockWalletProviderAdapter(
            secret=secret
        )

        try:
            payload = adapter.verify_and_parse(
                raw_body=raw_body,
                headers=request.headers,
            )
        except WalletProviderSignatureError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except WalletProviderPayloadError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = MockTopUpWebhookSerializer(
            data=payload
        )
        serializer.is_valid(
            raise_exception=True
        )

        data = serializer.validated_data

        try:
            if (
                data["status"]
                == WalletTopUp.Status.SUCCESS
            ):
                topup, wallet_transaction, processed = (
                    confirm_wallet_topup(
                        topup_id=data["topup_id"],
                        provider=data["provider"],
                        provider_reference=data[
                            "provider_reference"
                        ],
                        confirmed_amount=data["amount"],
                    )
                )

                transaction_id = (
                    wallet_transaction.pk
                )

            else:
                topup, processed = fail_wallet_topup(
                    topup_id=data["topup_id"],
                    provider=data["provider"],
                    provider_reference=data[
                        "provider_reference"
                    ],
                    amount=data["amount"],
                    failure_reason=data[
                        "failure_reason"
                    ],
                )

                transaction_id = None

        except (
            WalletTopUpConflictError,
            WalletTopUpStateError,
            WalletNotActiveError,
        ) as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )

        except (
            WalletTopUpError,
            WalletError,
        ) as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "processed": processed,
                "wallet_transaction_id":
                    transaction_id,
                "topup":
                    WalletTopUpSerializer(topup).data,
            },
            status=status.HTTP_200_OK,
        )
