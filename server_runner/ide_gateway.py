"""
Authorization helpers for the CodeView browser IDE gateway.

Responsibilities:
1. Verify developer access.
2. Request the developer's IDE from the trusted Runner.
3. Validate the returned sandbox identity.

HTTP and WebSocket proxying will be implemented separately.
"""

from uuid import UUID

from asgiref.sync import sync_to_async

from community.models import ProjectGroup
from server_runner.client import CodeExecutionRunnerClient
from server_runner.services import authorize_developer_ide_access


@sync_to_async
def authorize_ide_request(user, group_id):
    """
    Verify that a developer can access their own IDE.

    Django database operations run in a synchronous function.
    """

    # STEP 1: Reject unauthenticated users.
    if not user.is_authenticated:
        raise PermissionError("You must log in first.")

    # STEP 2: Find the requested private group.
    group = ProjectGroup.objects.filter(
        id=group_id,
        is_private=True,
    ).first()

    if group is None:
        raise PermissionError("Private development group not found.")

    # STEP 3: Reuse CodeView's existing authorization.
    authorize_developer_ide_access(
        user=user,
        group=group,
        developer_id=user.pk,
    )

    # STEP 4: Return the trusted developer and group IDs.
    return user.pk, group.pk


async def get_authorized_ide(user, group_id):
    """
    Authorize the developer and obtain their sandbox identity.

    Never accept a sandbox UUID directly from the browser.
    """

    # STEP 1: Verify the developer's permissions.
    developer_id, authorized_group_id = await authorize_ide_request(
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
    if result.get("status") != "running" or result.get("ready") is not True:
        raise RuntimeError("Developer IDE is not running.")

    # STEP 5: Validate the sandbox UUID returned by the Runner.
    try:
        sandbox_id = UUID(result["sandbox_id"])

    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(
            "Runner returned an invalid sandbox identity."
        ) from error

    if sandbox_id.int == 0:
        raise RuntimeError("Runner returned an invalid sandbox identity.")

    # STEP 6: Return the validated sandbox UUID.
    return sandbox_id