"""
core/serializers_otp.py
Serializers pour les endpoints OTP.
"""
from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()


class OTPRequestSerializer(serializers.Serializer):
    """POST /auth/otp/request/ — demande d'envoi d'OTP."""
    phone = serializers.CharField(max_length=20)

    def validate_phone(self, value):
        value = value.strip()
        if not value.startswith("+"):
            raise serializers.ValidationError(
                "Le numéro doit être au format international (ex: +237xxxxxxxxx)."
            )
        return value


class OTPVerifySerializer(serializers.Serializer):
    """POST /auth/otp/verify/ — vérification du code OTP."""
    phone = serializers.CharField(max_length=20)
    code = serializers.CharField(min_length=6, max_length=6)

    def validate_phone(self, value):
        return value.strip()

    def validate_code(self, value):
        if not value.isdigit():
            raise serializers.ValidationError("Le code doit contenir uniquement des chiffres.")
        return value


class OTPLoginSerializer(serializers.Serializer):
    """POST /auth/otp/login/ — connexion via OTP (retourne tokens JWT)."""
    phone = serializers.CharField(max_length=20)
    code = serializers.CharField(min_length=6, max_length=6)

    def validate_phone(self, value):
        return value.strip()

    def validate_code(self, value):
        if not value.isdigit():
            raise serializers.ValidationError("Le code doit contenir uniquement des chiffres.")
        return value


class PhoneVerifiedResponseSerializer(serializers.Serializer):
    """Schéma de réponse pour la vérification OTP réussie."""
    detail = serializers.CharField()
    phone_verified = serializers.BooleanField()