"""Persistent replay protection and IDE operation leases using PostgreSQL."""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server_runner.sandbox_controller.operation import (
    SandboxOperation,
    SandboxOperationRequest,
)

from server_runner.sandbox_controller.replay import (
    ReplayProtectionError,
)


class DatabaseReplayStore:
    """Claim sandbox requests and assign ownership to IDE operations."""

    # Initial lease duration for persistent IDE operations.
    # Active workers must renew leases before they expire.
    IDE_LEASE_SECONDS = 120

    def __init__(self, db: Session):
        """Receive an existing SQLAlchemy database session."""

        self.db = db

    def claim_request(self, request: SandboxOperationRequest):
        """
        Register an operation request in PostgreSQL.

        Persistent IDE operations receive an ownership lease.

        Temporary RUN operations retain their original behavior.

        IMPORTANT:
        The caller owns the transaction.
        This function does not commit or roll back.
        """

        # ----------------------------------------------------
        # STEP 1: VALIDATE THE REQUEST
        # ----------------------------------------------------

        if not isinstance(request, SandboxOperationRequest):
            raise ValueError("Invalid sandbox operation request.")

        if not isinstance(request.request_id, UUID):
            raise ValueError("Invalid request ID.")

        # ----------------------------------------------------
        # STEP 2: IDENTIFY PERSISTENT IDE OPERATIONS
        # ----------------------------------------------------

        is_ide_operation = request.operation in {
            SandboxOperation.CREATE,
            SandboxOperation.START,
            SandboxOperation.STOP,
            SandboxOperation.INSPECT,
        }

        # RUN operations do not require a persistent IDE lease.
        lease_owner = uuid4() if is_ide_operation else None

        # Each new IDE lease starts with generation 1.
        lease_generation = 1 if is_ide_operation else 0

        # ----------------------------------------------------
        # STEP 3: INSERT THE OPERATION RECORD
        # ----------------------------------------------------

        try:
            self.db.execute(
                text(
                    """
                    INSERT INTO runner.sandbox_operation_requests (
                        request_id,
                        sandbox_id,
                        developer_id,
                        group_id,
                        operation,
                        lease_owner,
                        lease_expires_at,
                        lease_generation
                    )
                    VALUES (
                        :request_id,
                        :sandbox_id,
                        :developer_id,
                        :group_id,
                        :operation,
                        :lease_owner,
                        CASE
                            WHEN :lease_owner IS NULL THEN NULL
                            ELSE NOW() + (
                                :lease_seconds * INTERVAL '1 second'
                            )
                        END,
                        :lease_generation
                    )
                    """
                ),
                {
                    "request_id": request.request_id,
                    "sandbox_id": request.sandbox_id,
                    "developer_id": request.developer_id,
                    "group_id": request.group_id,
                    "operation": request.operation.value,
                    "lease_owner": lease_owner,
                    "lease_seconds": self.IDE_LEASE_SECONDS,
                    "lease_generation": lease_generation,
                },
            )

        except IntegrityError as error:

            # PostgreSQL uses SQLSTATE 23505 for unique violations.
            sqlstate = getattr(
                getattr(error.orig, "diag", None),
                "sqlstate",
                None,
            ) or getattr(error.orig, "sqlstate", None)

            constraint_name = getattr(
                getattr(error.orig, "diag", None),
                "constraint_name",
                None,
            )

            if (
                sqlstate == "23505"
                and constraint_name == "sandbox_operation_requests_pkey"
            ):
                raise ReplayProtectionError(
                    "Sandbox operation request has already been used."
                ) from error

            raise

        # The caller commits the operation claim.
        return True

    def get_lease(self, request_id: UUID):
        """
        Retrieve the current lease for an operation.

        Returns None if the operation does not exist.
        """

        if not isinstance(request_id, UUID):
            raise ValueError("Invalid request ID.")

        result = self.db.execute(
            text(
                """
                SELECT
                    lease_owner,
                    lease_expires_at,
                    lease_generation,
                    status
                FROM runner.sandbox_operation_requests
                WHERE request_id = :request_id
                """
            ),
            {"request_id": request_id},
        ).mappings().one_or_none()

        return result

    def renew_lease(
        self,
        request_id: UUID,
        lease_owner: UUID,
        lease_generation: int,
    ):
        """
        Renew an IDE lease only if the caller still owns it.

        Returns True when renewed, otherwise False.

        The caller owns the transaction.
        """

        # Validate the request ID.
        if not isinstance(request_id, UUID):
            raise ValueError("Invalid request ID.")

        # Validate the lease owner.
        if not isinstance(lease_owner, UUID):
            raise ValueError("Invalid lease owner.")

        # Validate the lease generation.
        if (
            isinstance(lease_generation, bool)
            or not isinstance(lease_generation, int)
            or lease_generation < 1
        ):
            raise ValueError("Invalid lease generation.")

        # Prevent waiting indefinitely for a PostgreSQL row lock.
        #
        # SET LOCAL applies only to the current transaction.
        # PostgreSQL automatically resets it when the
        # transaction commits or rolls back.
        self.db.execute(
            text("SET LOCAL lock_timeout = '5s'")
        )

        # Renew only when the operation is still pending
        # and this worker still owns an unexpired lease.
        result = self.db.execute(
            text(
                """
                UPDATE runner.sandbox_operation_requests
                SET lease_expires_at = NOW() + (
                    :lease_seconds * INTERVAL '1 second'
                )
                WHERE request_id = :request_id
                  AND lease_owner = :lease_owner
                  AND lease_generation = :lease_generation
                  AND lease_expires_at > NOW()
                  AND status = 'pending'
                """
            ),
            {
                "request_id": request_id,
                "lease_owner": lease_owner,
                "lease_generation": lease_generation,
                "lease_seconds": self.IDE_LEASE_SECONDS,
            },
        )

        # True means the operation lease was renewed.
        # False means no matching active lease was found.
        return result.rowcount == 1

    def owns_lease(
        self,
        request_id: UUID,
        lease_owner: UUID,
        lease_generation: int,
    ):
        """
        Check whether an operation is still owned by this worker.

        Returns True only for a valid, unexpired pending lease.
        """

        if not isinstance(request_id, UUID):
            raise ValueError("Invalid request ID.")

        if not isinstance(lease_owner, UUID):
            raise ValueError("Invalid lease owner.")

        if (
            isinstance(lease_generation, bool)
            or not isinstance(lease_generation, int)
            or lease_generation < 1
        ):
            raise ValueError("Invalid lease generation.")

        result = self.db.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM runner.sandbox_operation_requests
                    WHERE request_id = :request_id
                      AND lease_owner = :lease_owner
                      AND lease_generation = :lease_generation
                      AND lease_expires_at > NOW()
                      AND status = 'pending'
                )
                """
            ),
            {
                "request_id": request_id,
                "lease_owner": lease_owner,
                "lease_generation": lease_generation,
            },
        ).scalar_one()

        return bool(result)