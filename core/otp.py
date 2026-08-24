"""
core/otp.py
Utilitaires OTP (cache) : génération, validation, invalidation.
"""
import random
import string
import logging
from django.core.cache import cache

logger = logging.getLogger(__name__)

OTP_LENGTH = 6
OTP_TTL = 300          # 5 minutes
OTP_MAX_ATTEMPTS = 5   # tentatives max
OTP_COOLDOWN = 60      # cooldown avant renvoi


def _otp_key(phone: str) -> str:
    return f"otp:code:{phone}"


def _attempts_key(phone: str) -> str:
    return f"otp:attempts:{phone}"


def _cooldown_key(phone: str) -> str:
    return f"otp:cooldown:{phone}"


def generate_otp(phone: str) -> str:
    if cache.get(_cooldown_key(phone)):
        raise ValueError("Please wait before requesting a new OTP.")

    code = "".join(random.choices(string.digits, k=OTP_LENGTH))
    cache.set(_otp_key(phone), code, timeout=OTP_TTL)

    cache.delete(_attempts_key(phone))
    cache.set(_cooldown_key(phone), True, timeout=OTP_COOLDOWN)

    logger.info("OTP generated for phone %s", phone)
    return code


def validate_otp(phone: str, code: str) -> bool:
    stored = cache.get(_otp_key(phone))
    if stored is None:
        return False

    attempts = cache.get(_attempts_key(phone), 0) + 1
    cache.set(_attempts_key(phone), attempts, timeout=OTP_TTL)

    if attempts > OTP_MAX_ATTEMPTS:
        cache.delete(_otp_key(phone))
        cache.delete(_attempts_key(phone))
        logger.warning("Too many OTP attempts for phone %s — OTP invalidated", phone)
        return False

    if stored != code:
        return False

    cache.delete(_otp_key(phone))
    cache.delete(_attempts_key(phone))
    logger.info("OTP validated successfully for phone %s", phone)
    return True


def invalidate_otp(phone: str) -> None:
    cache.delete(_otp_key(phone))
    cache.delete(_attempts_key(phone))
    cache.delete(_cooldown_key(phone))
