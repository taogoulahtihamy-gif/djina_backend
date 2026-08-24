from channels.generic.websocket import (
    AsyncJsonWebsocketConsumer,
)


ADMIN_REALTIME_GROUP = (
    'djina_admin_realtime'
)


def is_admin_user(user):
    if (
        not user or
        not user.is_authenticated or
        not user.is_active
    ):
        return False

    if (
        getattr(
            user,
            'is_superuser',
            False,
        )
        is True
    ):
        return True

    if (
        getattr(
            user,
            'is_staff',
            False,
        )
        is True
    ):
        return True

    admin_type = getattr(
        user,
        'admin_type',
        None,
    )

    if admin_type in {
        'admin',
        'super',
        'super_admin',
    }:
        return True

    role = getattr(
        user,
        'role',
        None,
    )

    if role in {
        'admin',
        'ADMIN',
        'super_admin',
        'SUPER_ADMIN',
    }:
        return True

    return False


class AdminRealtimeConsumer(
    AsyncJsonWebsocketConsumer,
):
    async def connect(self):
        user = self.scope.get(
            'user',
        )

        if not is_admin_user(
            user,
        ):
            await self.close(
                code=4403,
            )

            return

        await self.channel_layer.group_add(
            ADMIN_REALTIME_GROUP,
            self.channel_name,
        )

        await self.accept()

        await self.send_json({
            'type':
                'connection',

            'status':
                'connected',
        })


    async def disconnect(
        self,
        close_code,
    ):
        await self.channel_layer.group_discard(
            ADMIN_REALTIME_GROUP,
            self.channel_name,
        )


    async def admin_event(
        self,
        event,
    ):
        await self.send_json({
            'type':
                'admin_event',

            'resource':
                event.get(
                    'resource',
                ),

            'action':
                event.get(
                    'action',
                ),

            'object_id':
                event.get(
                    'object_id',
                ),
        })