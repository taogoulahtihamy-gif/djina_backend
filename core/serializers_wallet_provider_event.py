from rest_framework import serializers

from core.models import WalletProviderEvent


class WalletProviderEventAdminSerializer(
    serializers.ModelSerializer
):
    topup_id = serializers.IntegerField(
        read_only=True,
        allow_null=True,
    )

    wallet_transaction_id = (
        serializers.IntegerField(
            read_only=True,
            allow_null=True,
        )
    )

    class Meta:
        model = WalletProviderEvent

        fields = (
            "id",
            "topup_id",
            "reported_topup_id",
            "provider",
            "provider_reference",
            "callback_status",
            "provider_status",
            "amount",
            "failure_reason",
            "outcome",
            "processed",
            "wallet_transaction_id",
            "error_type",
            "error_message",
            "completed_at",
            "created_at",
            "updated_at",
        )

        read_only_fields = fields
