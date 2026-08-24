"""
core/sms.py
Abstraction SMS avec adaptateurs Twilio et Africa's Talking.

Configurez SMS_BACKEND dans settings.py:
  SMS_BACKEND = "core.sms.ConsoleSMSBackend"  (ou TwilioSMSBackend / AfricasTalkingBackend)
"""
import logging
from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


class BaseSMSBackend:
    def send(self, to: str, message: str) -> bool:
        raise NotImplementedError


class ConsoleSMSBackend(BaseSMSBackend):
    def send(self, to: str, message: str) -> bool:
        print(f"\n[SMS → {to}]\n{message}\n")
        logger.debug("ConsoleSMSBackend → %s : %s", to, message)
        return True


class TwilioSMSBackend(BaseSMSBackend):
    def __init__(self):
        try:
            from twilio.rest import Client  # type: ignore
        except ImportError as exc:
            raise ImportError("Install twilio: pip install twilio") from exc

        account_sid = getattr(settings, "TWILIO_ACCOUNT_SID", None)
        auth_token = getattr(settings, "TWILIO_AUTH_TOKEN", None)
        from_number = (
            getattr(settings, "TWILIO_FROM_NUMBER", None)
            or getattr(settings, "SMS_FROM", None)
        )

        if not account_sid or not auth_token or not from_number:
            raise ValueError("Twilio settings missing: TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER")

        self._client = Client(account_sid, auth_token)
        self._from = from_number

    def send(self, to: str, message: str) -> bool:
        try:
            msg = self._client.messages.create(body=message, from_=self._from, to=to)
            logger.info("Twilio SMS sent to %s — SID %s", to, msg.sid)
            return True
        except Exception as exc:
            logger.error("Twilio SMS error to %s: %s", to, exc)
            return False


class AfricasTalkingBackend(BaseSMSBackend):
    def __init__(self):
        try:
            import africastalking  # type: ignore
        except ImportError as exc:
            raise ImportError("Install africastalking: pip install africastalking") from exc

        username = getattr(settings, "AFRICAS_TALKING_USERNAME", None)
        api_key = getattr(settings, "AFRICAS_TALKING_API_KEY", None)
        if not username or not api_key:
            raise ValueError("Africa's Talking settings missing: AFRICAS_TALKING_USERNAME / AFRICAS_TALKING_API_KEY")

        africastalking.initialize(username=username, api_key=api_key)
        self._sms = africastalking.SMS
        self._sender = getattr(settings, "AFRICAS_TALKING_SENDER", None) or None

    def send(self, to: str, message: str) -> bool:
        try:
            kwargs = {"message": message, "recipients": [to]}
            if self._sender:
                kwargs["sender_id"] = self._sender
            resp = self._sms.send(**kwargs)
            logger.info("Africa's Talking SMS sent to %s — resp: %s", to, resp)
            return True
        except Exception as exc:
            logger.error("Africa's Talking SMS error to %s: %s", to, exc)
            return False


def get_sms_backend() -> BaseSMSBackend:
    backend_path = getattr(settings, "SMS_BACKEND", "core.sms.ConsoleSMSBackend")
    backend_cls = import_string(backend_path)

    try:
        return backend_cls()
    except Exception as exc:
        # ✅ fallback total: si import twilio absent, settings manquants, etc.
        logger.warning(
            "SMS backend %s failed (%s). Falling back to ConsoleSMSBackend.",
            backend_path,
            exc,
        )
        return ConsoleSMSBackend()


def send_otp_sms(phone: str, code: str) -> bool:
    message = (
        f"Votre code de vérification est : {code}\n"
        f"Il expire dans 5 minutes. Ne le partagez jamais."
    )
    return get_sms_backend().send(phone, message)
