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

    # Identify the developer who owns the IDE.
    developer_id: int

    # Identify the private development group.
    group_id: int

    # Identify the developer's Docker sandbox.
    sandbox_id: UUID

    # Identify the specific group membership used for authorization.
    membership_generation: UUID


def consume_ide_grant(
    db: Session,
    token: str,
) -> AuthorizedIDEIdentity:
    """
    Verify and consume a one-time IDE authorization grant.

    The grant must:
    1. Have a valid signature and expiration.
    2. Contain a valid membership generation.
    3. Match an existing developer sandbox.
    4. Have an unused grant ID.

    Return the developer, group, sandbox, and signed
    membership generation.

    IMPORTANT:
    This function does not yet check whether the membership
    generation is still active in Django's database.
    """

    # ========================================================
    # STEP 1: VERIFY THE SIGNED AUTHORIZATION GRANT
    # ========================================================

    try:
        # Verify the grant's signature, expiration, and fields.
        payload = verify_ide_grant(token)

    except InvalidIDEGrant as error:
        raise IDEGrantRejected(
            "Invalid or expired IDE authorization."
        ) from error

    # ========================================================
    # STEP 2: EXTRACT THE VERIFIED GRANT INFORMATION
    # ========================================================

    # Identify the developer's private Docker sandbox.
    sandbox_id = UUID(payload["sandbox_id"])

    # Identify the developer requesting access.
    developer_id = payload["developer_id"]

    # Identify the private development group.
    group_id = payload["group_id"]

    # Identify this unique authorization grant.
    grant_id = payload["grant_id"]

    # Convert the signed membership generation into a UUID.
    #
    # verify_ide_grant() has already validated its format.
    membership_generation = UUID(
        payload["membership_generation"]
    )

    # ========================================================
    # STEP 3: VERIFY SANDBOX OWNERSHIP
    # ========================================================

    # Find a sandbox matching the signed developer,
    # private group, and sandbox UUID.
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

    # ========================================================
    # STEP 4: VERIFY THE DOCKER CONTAINER EXISTS
    # ========================================================

    # The developer must have an existing Docker IDE container.
    if sandbox.container_id is None:
        raise IDEGrantRejected(
            "Developer IDE has not been created."
        )

    # ========================================================
    # STEP 5: PREPARE THE CONSUMED-GRANT RECORD
    # ========================================================

    # Record the grant ID so it cannot be consumed twice.
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

    # ========================================================
    # STEP 6: CONSUME THE GRANT ATOMICALLY
    # ========================================================

    try:
        # Add the consumed grant to the database transaction.
        db.add(consumed_grant)

        # PostgreSQL's unique constraint prevents duplicate
        # grant IDs from being committed.
        db.commit()

    except IntegrityError as error:
        # Undo the failed transaction.
        db.rollback()

        # Reject grants that have already been consumed.
        raise IDEGrantRejected(
            "IDE authorization grant has already been used."
        ) from error

    # ========================================================
    # STEP 7: RETURN THE AUTHORIZED IDE IDENTITY
    # ========================================================

    # Return the developer, group, sandbox, and generation.
    #
    # The Gateway will use this information when creating
    # a browser session. Current membership must still be
    # checked against an authoritative source.
    return AuthorizedIDEIdentity(
        developer_id=developer_id,
        group_id=group_id,
        sandbox_id=sandbox_id,
        membership_generation=membership_generation,
    )