# core/views_otp.py
from django.contrib.auth import get_user_model
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from drf_spectacular.utils import extend_schema

from .otp import generate_otp, validate_otp
from .sms import send_otp_sms
from .serializers_otp import (
    OTPRequestSerializer,
    OTPVerifySerializer,
    OTPLoginSerializer,
    PhoneVerifiedResponseSerializer,
)
from .serializers import UserSerializer

User = get_user_model()


def _success_response(message: str, data=None, status_code: int = 200):
    response = {"success": True, "status": status_code, "message": message}
    if data is not None:
        response["data"] = data
    return Response(response, status=status_code)


def _error_response(message: str, status_code: int = 400):
    return Response(
        {"success": False, "status": status_code, "message": message},
        status=status_code,
    )


class OTPViewSet(viewsets.GenericViewSet):
    queryset = User.objects.none()
    permission_classes = [AllowAny]

    @extend_schema(
        request=OTPRequestSerializer,
        responses=dict,
        description="Demande l'envoi d'un code OTP par SMS pour un numéro de téléphone.",
    )
    @action(detail=False, methods=["post"], url_path="request")
    def request_otp(self, request):
        serializer = OTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data["phone"]

        try:
            code = generate_otp(phone)
        except ValueError as exc:
            return _error_response(str(exc), 429)

        send_otp_sms(phone, code)
        return _success_response("Code OTP envoyé.")

    @extend_schema(
        request=OTPRequestSerializer,
        responses=dict,
        description="Renvoie un nouveau code OTP par SMS pour un numéro de téléphone (soumis au cooldown).",
    )
    @action(detail=False, methods=["post"], url_path="resend")
    def resend_otp(self, request):
        serializer = OTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data["phone"]

        try:
            code = generate_otp(phone)
        except ValueError as exc:
            return _error_response(str(exc), 429)

        send_otp_sms(phone, code)
        return _success_response("Code OTP renvoyé.")

    @extend_schema(
        request=OTPVerifySerializer,
        responses=PhoneVerifiedResponseSerializer,
        description="Vérifie un code OTP et marque le téléphone comme vérifié.",
    )
    @action(detail=False, methods=["post"], url_path="verify")
    def verify_otp(self, request):
        serializer = OTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data["phone"]
        code = serializer.validated_data["code"]

        if not validate_otp(phone, code):
            return _error_response("Code OTP invalide ou expiré.", 400)

        User.objects.filter(phone=phone).update(phone_verified=True)
        return _success_response(
            "Téléphone vérifié.",
            {"detail": "Téléphone vérifié.", "phone_verified": True},
        )

    @extend_schema(
        request=OTPLoginSerializer,
        responses=dict,
        description="Connexion via OTP : retourne les tokens JWT (access + refresh).",
    )
    @action(detail=False, methods=["post"], url_path="login")
    def login_otp(self, request):
        serializer = OTPLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data["phone"]
        code = serializer.validated_data["code"]

        if not validate_otp(phone, code):
            return _error_response("Code OTP invalide ou expiré.", 400)

        try:
            user = User.objects.get(phone=phone, deleted_at__isnull=True)
        except User.DoesNotExist:
            return _error_response("Aucun compte associé à ce numéro.", 404)

        user.phone_verified = True
        user.save(update_fields=["phone_verified"])

        refresh = RefreshToken.for_user(user)
        return _success_response(
            "Connexion réussie.",
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user).data,
            },
        )
