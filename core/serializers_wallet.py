from rest_framework import serializers

from core.models import (
    DriverWallet,
    WalletTopUp,
    WalletTransaction,
)


class DriverWalletSerializer(serializers.ModelSerializer):
    available_balance = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        read_only=True,
    )

    class Meta:
        model = DriverWallet
        fields = (
            "id",
            "balance",
            "reserved_balance",
            "available_balance",
            "currency",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = (
            "id",
            "type",
            "direction",
            "amount",
            "balance_before",
            "balance_after",
            "course",
            "commission",
            "provider",
            "provider_reference",
            "status",
            "created_at",
        )
        read_only_fields = fields


class WalletTopUpSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTopUp
        fields = (
            "id",
            "amount",
            "currency",
            "provider",
            "phone",
            "provider_reference",
            "provider_status",
            "initiated_at",
            "idempotency_key",
            "status",
            "requested_at",
            "confirmed_at",
            "failure_reason",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class WalletTopUpCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
    )

    provider = serializers.ChoiceField(
        choices=WalletTopUp.Provider.choices,
    )

    phone = serializers.CharField(
        max_length=20,
        trim_whitespace=True,
    )

    idempotency_key = serializers.CharField(
        max_length=120,
        trim_whitespace=True,
    )
