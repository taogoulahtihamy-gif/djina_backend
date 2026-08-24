"""
tests/test_otp.py
Tests unitaires OTP — pytest + Django test client.
"""
import pytest
from django.core.cache import cache
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from core.otp import generate_otp, validate_otp, invalidate_otp, _otp_key

User = get_user_model()

PHONE = "+237690000001"


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def force_console_sms_backend(settings):
    """
    Force le backend SMS console pendant les tests.
    Empêche Twilio d'être instancié (et donc évite TWILIO_FROM_NUMBER, etc.)
    """
    settings.SMS_BACKEND = "core.sms.ConsoleSMSBackend"


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="test@example.com",
        phone=PHONE,
        password="secure123",
    )


@pytest.fixture
def client():
    return APIClient()


# ---------------------------------------------------------------
# Tests unitaires generate_otp / validate_otp
# ---------------------------------------------------------------
class TestOTPUtils:
    def test_generate_returns_6_digits(self, db):
        code = generate_otp(PHONE)
        assert len(code) == 6
        assert code.isdigit()

    def test_validate_correct_code(self, db):
        code = generate_otp(PHONE)
        assert validate_otp(PHONE, code) is True

    def test_validate_wrong_code(self, db):
        generate_otp(PHONE)
        assert validate_otp(PHONE, "000000") is False

    def test_validate_expired_otp(self, db):
        generate_otp(PHONE)
        cache.delete(_otp_key(PHONE))
        assert validate_otp(PHONE, "123456") is False

    def test_code_consumed_after_success(self, db):
        code = generate_otp(PHONE)
        assert validate_otp(PHONE, code) is True
        assert validate_otp(PHONE, code) is False

    def test_cooldown_blocks_second_request(self, db):
        generate_otp(PHONE)
        with pytest.raises(ValueError, match="Please wait"):
            generate_otp(PHONE)

    def test_invalidate_clears_code(self, db):
        code = generate_otp(PHONE)
        invalidate_otp(PHONE)
        assert validate_otp(PHONE, code) is False

    def test_too_many_attempts_invalidates(self, db):
        code = generate_otp(PHONE)
        for _ in range(5):
            validate_otp(PHONE, "999999")
        assert validate_otp(PHONE, code) is False


# ---------------------------------------------------------------
# Tests des endpoints via APIClient
# ---------------------------------------------------------------
@pytest.mark.django_db
class TestOTPEndpoints:
    BASE = "/api/auth/otp"

    def test_request_otp_unknown_phone(self, client):
        resp = client.post(f"{self.BASE}/request/", {"phone": "+221786032827"})
        assert resp.status_code == 200

    def test_request_otp_known_phone(self, client, user):
        resp = client.post(f"{self.BASE}/request/", {"phone": PHONE})
        assert resp.status_code == 200
        assert "detail" in resp.data

    def test_request_otp_invalid_format(self, client, user):
        resp = client.post(f"{self.BASE}/request/", {"phone": "0690000001"})
        assert resp.status_code == 400

    def test_request_otp_cooldown(self, client, user):
        client.post(f"{self.BASE}/request/", {"phone": PHONE})
        resp = client.post(f"{self.BASE}/request/", {"phone": PHONE})
        assert resp.status_code == 429

    def test_verify_otp_success(self, client, user):
        code = generate_otp(PHONE)
        resp = client.post(f"{self.BASE}/verify/", {"phone": PHONE, "code": code})
        assert resp.status_code == 200
        assert resp.data["phone_verified"] is True
        user.refresh_from_db()
        assert user.phone_verified is True

    def test_verify_otp_wrong_code(self, client, user):
        generate_otp(PHONE)
        resp = client.post(f"{self.BASE}/verify/", {"phone": PHONE, "code": "000000"})
        assert resp.status_code == 400

    def test_login_otp_success(self, client, user):
        code = generate_otp(PHONE)
        resp = client.post(f"{self.BASE}/login/", {"phone": PHONE, "code": code})
        assert resp.status_code == 200
        assert "access" in resp.data
        assert "refresh" in resp.data
        assert resp.data["user_type"] == user.user_type

    def test_login_otp_wrong_code(self, client, user):
        generate_otp(PHONE)
        resp = client.post(f"{self.BASE}/login/", {"phone": PHONE, "code": "999999"})
        assert resp.status_code == 400

    def test_resend_otp_requires_auth(self, client, user):
        resp = client.post(f"{self.BASE}/resend/")
        assert resp.status_code == 401

    def test_resend_otp_authenticated(self, client, user):
        client.force_authenticate(user=user)
        resp = client.post(f"{self.BASE}/resend/")
        assert resp.status_code == 200
        assert "detail" in resp.data
