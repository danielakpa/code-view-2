"""
Internal membership validation endpoint for CodeView IDE access.

The trusted IDE Gateway uses this endpoint to verify that
a developer still has permission to access a private group.

Never expose the service authentication token to browsers.
"""

import json
import secrets

from uuid import UUID

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from community.models import ProjectGroup

from server_runner.services import (
    get_developer_ide_membership_generation,
)

from django.contrib.auth import get_user_model


@csrf_exempt
@require_POST
def validate_ide_membership(request):
    """
    Validate a developer's current IDE membership.

    The request must contain:
    - A trusted internal service token.
    - A developer ID.
    - A private group ID.
    - A membership generation UUID.
    """

    # STEP 1: Load the internal service token.
    #
    # This token must be configured securely in Django.
    expected_token = getattr(
        settings,
        "CODEVIEW_IDE_MEMBERSHIP_TOKEN",
        None,
    )

    # STEP 2: Reject requests when authentication
    # has not been configured.
    if not isinstance(expected_token, str) or not expected_token:
        return JsonResponse(
            {"authorized": False},
            status=503,
        )

    # STEP 3: Read the Authorization header.
    authorization = request.headers.get(
        "Authorization",
        "",
    )

    # STEP 4: Require Bearer authentication.
    if not authorization.startswith("Bearer "):
        return JsonResponse(
            {"authorized": False},
            status=403,
        )

    supplied_token = authorization[len("Bearer "):]

    # STEP 5: Compare service tokens securely.
    if not secrets.compare_digest(
        supplied_token,
        expected_token,
    ):
        return JsonResponse(
            {"authorized": False},
            status=403,
        )

    # STEP 6: Parse the JSON request body.
    try:
        data = json.loads(request.body)

    except (ValueError, UnicodeDecodeError):
        return JsonResponse(
            {"authorized": False},
            status=400,
        )

    # STEP 7: Require a JSON object.
    if not isinstance(data, dict):
        return JsonResponse(
            {"authorized": False},
            status=400,
        )

    # STEP 8: Extract the supplied identities.
    developer_id = data.get("developer_id")
    group_id = data.get("group_id")
    supplied_generation = data.get(
        "membership_generation"
    )

    # STEP 9: Validate developer and group IDs.
    #
    # Reject boolean values because Python treats
    # bool as a subclass of int.
    if (
        type(developer_id) is not int
        or developer_id <= 0
        or type(group_id) is not int
        or group_id <= 0
    ):
        return JsonResponse(
            {"authorized": False},
            status=400,
        )

    # STEP 10: Validate the membership UUID.
    try:
        if not isinstance(supplied_generation, str):
            raise ValueError("Invalid generation.")

        membership_generation = UUID(
            supplied_generation
        )

        if membership_generation.int == 0:
            raise ValueError("Empty generation.")

    except (ValueError, TypeError, AttributeError):
        return JsonResponse(
            {"authorized": False},
            status=400,
        )

    # STEP 11: Find the developer.
    User = get_user_model()

    developer = User.objects.filter(
        pk=developer_id,
        is_active=True,
    ).first()

    if developer is None:
        return JsonResponse(
            {"authorized": False},
            status=403,
        )

    # STEP 12: Find the private development group.
    group = ProjectGroup.objects.filter(
        pk=group_id,
        is_private=True,
    ).first()

    if group is None:
        return JsonResponse(
            {"authorized": False},
            status=403,
        )

    # STEP 13: Retrieve the current generation
    # from Django's trusted database.
    try:
        current_generation = (
            get_developer_ide_membership_generation(
                user=developer,
                group=group,
            )
        )

    except PermissionError:
        return JsonResponse(
            {"authorized": False},
            status=403,
        )

    # STEP 14: Compare the supplied generation
    # against the current generation.
    if current_generation != membership_generation:
        return JsonResponse(
            {"authorized": False},
            status=403,
        )

    # STEP 15: Authorization succeeded.
    return JsonResponse(
        {
            "authorized": True,
            "developer_id": developer_id,
            "group_id": group_id,
        },
        status=200,
    )