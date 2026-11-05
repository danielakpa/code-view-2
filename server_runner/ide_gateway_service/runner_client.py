"""Internal Runner API client for the CodeView IDE Gateway."""

from dataclasses import dataclass
from uuid import UUID

import httpx

from server_runner.ide_gateway_service.config import (
    get_gateway_settings,
)


@dataclass(frozen=True)
class VerifiedIDEIdentity:
    """
    Store the developer, group, and sandbox identities
    verified by the Runner.
    """

    developer_id: int
    group_id: int
    sandbox_id: UUID


class RunnerAuthorizationError(Exception):
    """Raised when the Runner rejects an IDE authorization grant."""


class RunnerUnavailableError(Exception):
    """Raised when the Runner service cannot be reached."""


async def consume_grant_with_runner(
    grant: str,
) -> VerifiedIDEIdentity:
    """
    Send an IDE authorization grant to the Runner.

    The Runner verifies:
    1. The grant signature.
    2. The grant expiration time.
    3. Sandbox ownership.
    4. Whether the grant has already been used.

    Return the verified developer, group, and sandbox identities.
    """

    # STEP 1: Validate the supplied grant.
    if not isinstance(grant, str) or not grant:
        raise RunnerAuthorizationError(
            "IDE authorization grant is missing."
        )

    # STEP 2: Load the Gateway configuration.
    settings = get_gateway_settings()

    # STEP 3: Prepare authentication headers.
    headers = {
        "Authorization": f"Bearer {settings.runner_token}",
    }

    # STEP 4: Send the grant to the Runner.
    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=False,
        ) as client:

            response = await client.post(
                f"{settings.runner_url}/internal/ide-grants/consume",
                headers=headers,
                json={"grant": grant},
            )

    except httpx.RequestError as error:
        raise RunnerUnavailableError(
            "Unable to contact the Runner service."
        ) from error

    # STEP 5: Handle rejected authorization.
    if response.status_code == 403:
        raise RunnerAuthorizationError(
            "Runner rejected the IDE authorization grant."
        )

    # STEP 6: Handle other unsuccessful responses.
    if response.status_code != 200:
        raise RunnerUnavailableError(
            "Runner returned an unexpected response."
        )

    # STEP 7: Validate the Runner response.
    try:
        data = response.json()

        # Confirm the Runner authorized access.
        if not isinstance(data, dict):
            raise ValueError("Invalid Runner response.")

        if data.get("authorized") is not True:
            raise ValueError("Runner did not authorize access.")

        # Extract the verified developer and group identities.
        developer_id = data["developer_id"]
        group_id = data["group_id"]

        # Reject invalid IDs, including boolean values.
        if (
            type(developer_id) is not int
            or developer_id <= 0
            or type(group_id) is not int
            or group_id <= 0
        ):
            raise ValueError(
                "Invalid developer or group identity."
            )

        # Extract and validate the sandbox UUID.
        sandbox_id = UUID(data["sandbox_id"])

        if sandbox_id.int == 0:
            raise ValueError("Invalid sandbox UUID.")

    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise RunnerUnavailableError(
            "Runner returned invalid authorization data."
        ) from error

    # STEP 8: Return all three verified identities.
    return VerifiedIDEIdentity(
        developer_id=developer_id,
        group_id=group_id,
        sandbox_id=sandbox_id,
    )