"""Validate and atomically consume IDE authorization grants."""

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


def consume_ide_grant(db: Session, token: str) -> UUID:
    """
    Verify and consume a one-time IDE authorization grant.

    The grant must:
    1. Have a valid signature and expiration.
    2. Match an existing developer sandbox.
    3. Have an unused grant ID.

    Return the authorized sandbox UUID.
    """

    # STEP 1: Verify the signed grant.
    try:
        payload = verify_ide_grant(token)

    except InvalidIDEGrant as error:
        raise IDEGrantRejected(
            "Invalid or expired IDE authorization."
        ) from error

    # STEP 2: Extract the trusted grant information.
    sandbox_id = UUID(payload["sandbox_id"])
    developer_id = payload["developer_id"]
    group_id = payload["group_id"]
    grant_id = payload["grant_id"]

    # STEP 3: Verify the sandbox belongs to this developer and group.
    sandbox = (
        db.query(SandboxRecord)
        .filter(
            SandboxRecord.sandbox_id == sandbox_id,
            SandboxRecord.developer_id == developer_id,
            SandboxRecord.group_id == group_id,
        )
        .first()
    )

    if sandbox is None:
        raise IDEGrantRejected(
            "Sandbox ownership verification failed."
        )

    # STEP 4: Confirm that the sandbox has a Docker container.
    if sandbox.container_id is None:
        raise IDEGrantRejected(
            "Developer IDE has not been created."
        )

    # STEP 5: Prepare the consumed-grant record.
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

    # STEP 6: Attempt an atomic database insertion.
    try:
        db.add(consumed_grant)
        db.commit()

    except IntegrityError as error:
        # Undo the failed transaction.
        db.rollback()

        # A duplicate grant ID indicates a replay attempt.
        raise IDEGrantRejected(
            "IDE authorization grant has already been used."
        ) from error

    # STEP 7: Return the authorized sandbox UUID.
    return sandbox_id
    