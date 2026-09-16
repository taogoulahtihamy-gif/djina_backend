from rest_framework import serializers

from core.models import DriverWallet, WalletTransaction


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
