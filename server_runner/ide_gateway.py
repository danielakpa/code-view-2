"""
Authorization helpers for the CodeView browser IDE gateway.

Responsibilities:
1. Verify developer access to private development groups.
2. Retrieve the developer's current membership generation.
3. Request the developer's IDE from the trusted Runner.
4. Validate the returned sandbox identity.

Never trust developer, group, or sandbox identities supplied
directly by the browser.
"""

from uuid import UUID

from asgiref.sync import sync_to_async

from community.models import ProjectGroup

from server_runner.client import CodeExecutionRunnerClient

from server_runner.services import (
    get_developer_ide_membership_generation,
)


@sync_to_async
def authorize_ide_request(user, group_id):
    """
    Verify that a developer can access their own IDE.

    Return:
        developer_id
        group_id
        membership_generation

    Django database operations run synchronously.
    """

    # STEP 1: Reject unauthenticated users.
    if not user.is_authenticated:
        raise PermissionError(
            "You must log in first."
        )

    # STEP 2: Find the requested private development group.
    group = ProjectGroup.objects.filter(
        id=group_id,
        is_private=True,
    ).first()

    # STEP 3: Reject missing or public groups.
    if group is None:
        raise PermissionError(
            "Private development group not found."
        )

    # STEP 4: Verify the developer's permissions and retrieve
    # the current membership generation from Django.
    #
    # Group owners use ProjectGroup.owner_membership_generation.
    # Regular members use GroupMember.membership_generation.
    membership_generation = (
        get_developer_ide_membership_generation(
            user=user,
            group=group,
        )
    )

    # STEP 5: Reject an invalid membership generation.
    if (
        not isinstance(membership_generation, UUID)
        or membership_generation.int == 0
    ):
        raise PermissionError(
            "Invalid developer membership generation."
        )

    # STEP 6: Return the trusted identities.
    return (
        user.pk,
        group.pk,
        membership_generation,
    )


async def get_authorized_ide(user, group_id):
    """
    Authorize the developer and obtain their sandbox identity.

    Never accept a sandbox UUID directly from the browser.
    """

    # STEP 1: Verify the developer's current permissions.
    (
        developer_id,
        authorized_group_id,
        membership_generation,
    ) = await authorize_ide_request(
        user=user,
        group_id=group_id,
    )

    # STEP 2: Create the existing Runner client.
    runner = CodeExecutionRunnerClient()

    # STEP 3: Ask the Runner to start or reuse this developer's IDE.
    result = await runner.ensure_ide(
        developer_id=developer_id,
        group_id=authorized_group_id,
    )

    # STEP 4: Confirm the Runner reports a running IDE.
    if (
        result.get("status") != "running"
        or result.get("ready") is not True
    ):
        raise RuntimeError(
            "Developer IDE is not running."
        )

    # STEP 5: Validate the sandbox UUID returned by the Runner.
    try:
        sandbox_id = UUID(result["sandbox_id"])

    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(
            "Runner returned an invalid sandbox identity."
        ) from error

    # STEP 6: Reject an empty sandbox UUID.
    if sandbox_id.int == 0:
        raise RuntimeError(
            "Runner returned an invalid sandbox identity."
        )

    # STEP 7: Recheck membership after the Runner operation.
    #
    # Starting an IDE may take time. The developer could be
    # removed from the group while the Runner is working.
    (
        current_developer_id,
        current_group_id,
        current_generation,
    ) = await authorize_ide_request(
        user=user,
        group_id=group_id,
    )

    # STEP 8: Reject changes to the authorized identity.
    if (
        current_developer_id != developer_id
        or current_group_id != authorized_group_id
        or current_generation != membership_generation
    ):
        raise PermissionError(
            "Developer membership changed during IDE startup."
        )

    # STEP 9: Return the validated sandbox UUID.
    #
    # Preserve the existing return type for callers that
    # currently expect only a UUID.
    return sandbox_id