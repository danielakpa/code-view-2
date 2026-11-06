"""Main URL configuration for CodeView."""

# Access Django project settings.
from django.conf import settings

# Helper for serving uploaded files during development.
from django.conf.urls.static import static

from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from home.views import home

# Import the internal IDE membership validation endpoint.
from server_runner.ide_membership_api import (
    validate_ide_membership,
)


# Django looks for this exact variable name: urlpatterns.
urlpatterns = [

    # Django admin panel.
    path("admin/", admin.site.urls),

    # Homepage.
    path("", home, name="home"),

    # Registration and login.
    path("", include("users.urls")),

    # Project Groups.
    path("groups/", include("community.urls")),

    # Redirect old Community URLs to Groups.
    path(
        "community/",
        RedirectView.as_view(
            pattern_name="groups",
            permanent=False,
        ),
    ),

    # ========================================
    # INTERNAL IDE MEMBERSHIP VALIDATION
    # ========================================

    # Used by the trusted IDE Gateway.
    #
    # Currently rejects every request.
    # Authentication and database validation come next.
    path(
        "internal/ide/membership/validate/",
        validate_ide_membership,
        name="internal_ide_membership_validate",
    ),
]


# ========================================
# SERVE UPLOADED FILES DURING DEVELOPMENT
# ========================================

# Only serve uploaded files this way in development.
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )