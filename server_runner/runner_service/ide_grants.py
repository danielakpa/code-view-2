"""Validate and atomically consume IDE authorization grants."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server_runner.ide_gateway_service.grants import (
    InvalidIDEGrant,
    verify_ide_grant,
)

from server_runner.runner_service.models import (
    IDEGrantConsumptionRecord,
    SandboxRecord,
)


class IDEGrantRejected(PermissionError):
    """Raised when an IDE grant cannot be accepted."""


@dataclass(frozen=True)
class AuthorizedIDEIdentity:
    """
    Store the verified identity of a developer's IDE.

    frozen=True prevents ordinary changes to these values
    after the object has been created.
    """

    developer_id: int
    group_id: int
    sandbox_id: UUID


def consume_ide_grant(
    db: Session,
    token: str,
) -> AuthorizedIDEIdentity:
    """
    Verify and consume a one-time IDE authorization grant.

    The grant must:
    1. Have a valid signature and expiration.
    2. Match an existing developer sandbox.
    3. Have an unused grant ID.

    Return the verified developer, group, and sandbox identity.
    """

    # STEP 1: Verify the signed authorization grant.
    try:
        payload = verify_ide_grant(token)

    except InvalidIDEGrant as error:
        raise IDEGrantRejected(
            "Invalid or expired IDE authorization."
        ) from error

    # STEP 2: Extract the verified grant information.
    sandbox_id = UUID(payload["sandbox_id"])
    developer_id = payload["developer_id"]
    group_id = payload["group_id"]
    grant_id = payload["grant_id"]

    # STEP 3: Verify that the sandbox belongs to this developer
    # and this private development group.
    sandbox = (
        db.query(SandboxRecord)
        .filter(
            SandboxRecord.sandbox_id == sandbox_id,
            SandboxRecord.developer_id == developer_id,
            SandboxRecord.group_id == group_id,
        )
        .first()
    )

    # Reject grants that do not match an existing sandbox.
    if sandbox is None:
        raise IDEGrantRejected(
            "Sandbox ownership verification failed."
        )

    # STEP 4: Confirm that the sandbox has a Docker container.
    if sandbox.container_id is None:
        raise IDEGrantRejected(
            "Developer IDE has not been created."
        )

    # STEP 5: Prepare the consumed-grant database record.
    consumed_grant = IDEGrantConsumptionRecord(
        grant_id=grant_id,
        sandbox_id=sandbox_id,
        developer_id=developer_id,
        group_id=group_id,
        expires_at=datetime.fromtimestamp(
            payload["expires_at"],
            tz=timezone.utc,
        ),
    )

    # STEP 6: Save the consumed grant atomically.
    # This prevents the same authorization grant from being reused.
    try:
        db.add(consumed_grant)
        db.commit()

    except IntegrityError as error:
        # Undo the failed database transaction.
        db.rollback()

        # Reject duplicate grant IDs.
        raise IDEGrantRejected(
            "IDE authorization grant has already been used."
        ) from error

    # STEP 7: Return all three verified identities.
    # These will later be used to create secure browser sessions.
    return AuthorizedIDEIdentity(
        developer_id=developer_id,
        group_id=group_id,
        sandbox_id=sandbox_id,
    )