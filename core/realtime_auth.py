from urllib.parse import (
    parse_qs,
)

from channels.db import (
    database_sync_to_async,
)

from django.contrib.auth import (
    get_user_model,
)

from django.contrib.auth.models import (
    AnonymousUser,
)

from rest_framework_simplejwt.exceptions import (
    InvalidToken,
    TokenError,
)

from rest_framework_simplejwt.tokens import (
    AccessToken,
)


User = get_user_model()


@database_sync_to_async
def get_user_from_token(
    raw_token,
):
    if not raw_token:
        return AnonymousUser()

    try:
        token = AccessToken(
            raw_token,
        )

        user_id = token.get(
            'user_id',
        )

        if not user_id:
            return AnonymousUser()

        user = User.objects.get(
            id=user_id,
        )

        if not user.is_active:
            return AnonymousUser()

        return user

    except (
        InvalidToken,
        TokenError,
        User.DoesNotExist,
        ValueError,
    ):
        return AnonymousUser()


class JWTAuthMiddleware:
    def __init__(
        self,
        inner,
    ):
        self.inner = inner


    async def __call__(
        self,
        scope,
        receive,
        send,
    ):
        scope = dict(scope)

        query_string = (
            scope
            .get(
                'query_string',
                b'',
            )
            .decode()
        )

        query_params = (
            parse_qs(
                query_string,
            )
        )

        raw_token = (
            query_params
            .get(
                'token',
                [None],
            )[0]
        )

        scope['user'] = (
            await get_user_from_token(
                raw_token,
            )
        )

        return await self.inner(
            scope,
            receive,
            send,
        )