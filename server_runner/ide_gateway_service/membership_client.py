"""
Trusted Django membership validation client for the IDE Gateway.

Responsibilities:
1. Contact Django's internal membership validation endpoint.
2. Authenticate using a dedicated service token.
3. Verify the developer's current membership generation.
4. Reject removed developers and outdated memberships.
5. Fail closed when Django is unavailable.
"""

import os

from uuid import UUID

import httpx


class MembershipAuthorizationError(Exception):
    """Raised when Django rejects IDE membership authorization."""


class MembershipServiceUnavailableError(Exception):
    """Raised when Django membership validation cannot be completed."""


async def validate_ide_membership(
    developer_id: int,
    group_id: int,
    membership_generation: UUID,
) -> bool:
    """
    Ask Django whether this developer's membership is still valid.

    Returns True only when Django explicitly authorizes access.

    Raises:
        MembershipAuthorizationError:
            Membership is invalid or has been revoked.

        MembershipServiceUnavailableError:
            Django cannot be contacted or returns invalid data.
    """

    # STEP 1: Validate the developer identity.
    if type(developer_id) is not int or developer_id <= 0:
        raise MembershipAuthorizationError(
            "Invalid developer identity."
        )

    # STEP 2: Validate the group identity.
    if type(group_id) is not int or group_id <= 0:
        raise MembershipAuthorizationError(
            "Invalid group identity."
        )

    # STEP 3: Validate the membership generation.
    if (
        not isinstance(membership_generation, UUID)
        or membership_generation.int == 0
    ):
        raise MembershipAuthorizationError(
            "Invalid membership generation."
        )

    # STEP 4: Load the trusted Django service URL.
    django_url = os.environ.get(
        "CODEVIEW_DJANGO_INTERNAL_URL",
        "",
    ).strip().rstrip("/")

    # STEP 5: Load the shared membership service token.
    service_token = os.environ.get(
        "CODEVIEW_IDE_MEMBERSHIP_TOKEN",
        "",
    )

    # STEP 6: Reject missing configuration.
    if not django_url or not service_token:
        raise MembershipServiceUnavailableError(
            "IDE membership validation is not configured."
        )

    # STEP 7: Build the trusted endpoint URL.
    endpoint = (
        f"{django_url}/internal/ide/membership/validate/"
    )

    # STEP 8: Prepare service authentication.
    headers = {
        "Authorization": f"Bearer {service_token}",
    }

    # STEP 9: Prepare the membership identity.
    payload = {
        "developer_id": developer_id,
        "group_id": group_id,
        "membership_generation": str(
            membership_generation
        ),
    }

    # STEP 10: Contact Django asynchronously.
    try:
        async with httpx.AsyncClient(
            timeout=5.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:

            response = await client.post(
                endpoint,
                headers=headers,
                json=payload,
            )

    except httpx.RequestError as error:
        raise MembershipServiceUnavailableError(
            "Unable to contact Django membership service."
        ) from error

    # STEP 11: Reject revoked or unauthorized memberships.
    if response.status_code == 403:
        raise MembershipAuthorizationError(
            "IDE membership authorization was rejected."
        )

    # STEP 12: Reject unexpected HTTP responses.
    if response.status_code != 200:
        raise MembershipServiceUnavailableError(
            "Django membership service returned an unexpected status."
        )

    # STEP 13: Parse the response.
    try:
        data = response.json()

    except (ValueError, TypeError) as error:
        raise MembershipServiceUnavailableError(
            "Django returned invalid membership data."
        ) from error

    # STEP 14: Require an explicit authorization decision.
    if not isinstance(data, dict):
        raise MembershipServiceUnavailableError(
            "Invalid membership response."
        )

    if data.get("authorized") is not True:
        raise MembershipAuthorizationError(
            "Django did not authorize IDE membership."
        )

    # STEP 15: Verify the returned identities.
    if (
        type(data.get("developer_id")) is not int
        or data["developer_id"] != developer_id
        or type(data.get("group_id")) is not int
        or data["group_id"] != group_id
    ):
        raise MembershipServiceUnavailableError(
            "Django returned mismatched membership identities."
        )

    # STEP 16: Authorization succeeded.
    return True