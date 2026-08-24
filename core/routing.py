from django.urls import (
    path,
)

from core.consumers import (
    AdminRealtimeConsumer,
)


websocket_urlpatterns = [
    path(
        'ws/admin/realtime/',
        AdminRealtimeConsumer.as_asgi(),
    ),
]