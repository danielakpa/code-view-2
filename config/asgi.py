"""
ASGI configuration for CodeView.

Handles:
1. HTTP requests using Django.
2. WebSocket connections using Django Channels.
3. Authentication for WebSocket connections.
4. Static files during local development.
"""

import os

from django.conf import settings
from django.core.asgi import get_asgi_application
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack


# ============================================================
# 1. CONFIGURE DJANGO SETTINGS
# ============================================================

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings"
)


# ============================================================
# 2. INITIALIZE DJANGO
# ============================================================

# Create the Django ASGI application.
django_asgi_app = get_asgi_application()


# ============================================================
# 3. ENABLE STATIC FILES IN DEVELOPMENT
# ============================================================

# Daphne does not automatically serve Django static files.
#
# During development, this handler serves:
# - CSS files
# - JavaScript files
# - Images
#
# In production, a reverse proxy or static-file server
# should serve these files instead.

if settings.DEBUG:

    django_asgi_app = ASGIStaticFilesHandler(
        django_asgi_app
    )


# ============================================================
# 4. IMPORT WEBSOCKET ROUTES
# ============================================================

from community.routing import websocket_urlpatterns


# ============================================================
# 5. CONFIGURE ASGI APPLICATION
# ============================================================

application = ProtocolTypeRouter({

    # Handle HTTP requests.
    "http": django_asgi_app,

    # Handle WebSocket connections.
    "websocket": AuthMiddlewareStack(

        URLRouter(
            websocket_urlpatterns
        )

    ),

})