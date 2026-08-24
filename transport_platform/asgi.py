"""
ASGI config for transport_platform project.
"""

import os

from django.core.asgi import get_asgi_application


os.environ.setdefault(
    'DJANGO_SETTINGS_MODULE',
    'transport_platform.settings',
)


# Django doit être initialisé AVANT
# d'importer nos modules WebSocket.
django_asgi_application = get_asgi_application()


from channels.routing import (
    ProtocolTypeRouter,
    URLRouter,
)

from core.realtime_auth import (
    JWTAuthMiddleware,
)

from core.routing import (
    websocket_urlpatterns,
)


application = ProtocolTypeRouter(
    {
        'http': django_asgi_application,

        'websocket': JWTAuthMiddleware(
            URLRouter(
                websocket_urlpatterns,
            )
        ),
    }
)