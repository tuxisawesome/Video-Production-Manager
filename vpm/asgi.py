import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vpm.settings')
django.setup()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

from recording.routing import websocket_urlpatterns
from recording.token_auth import TokenAuthMiddleware

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            TokenAuthMiddleware(
                URLRouter(websocket_urlpatterns)
            )
        )
    ),
})
