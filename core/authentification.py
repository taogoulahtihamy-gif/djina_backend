from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken


class SessionJWTAuthentication(JWTAuthentication):
    """
    Authentification JWT DJINA avec révocation de session.

    Les nouveaux access tokens contiennent un claim `sid`
    correspondant au JTI du refresh token.

    Les anciens tokens sans `sid` restent temporairement acceptés
    afin d'éviter de casser immédiatement les sessions déjà émises.
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)

        session_id = validated_token.get("sid")

        
if not session_id:
    raise AuthenticationFailed(
        "Cette ancienne session n'est plus prise en charge. Veuillez vous reconnecter.",
        code="session_not_supported",
    

        if BlacklistedToken.objects.filter(
            token__jti=session_id
        ).exists():
            raise AuthenticationFailed(
                "Cette session a été fermée.",
                code="session_revoked",
            )

        return user