"""Recover interrupted persistent Docker IDE operations safely."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from server_runner.runner_service.models import SandboxRecord

from server_runner.sandbox_controller.developer_ide import (
    DeveloperIDEService,
)

from server_runner.sandbox_controller.lease_heartbeat import (
    IDELeaseHeartbeat,
)


# ============================================================
# 1. RECOVERY RESULT
# ============================================================

@dataclass(frozen=True)
class RecoveryResult:
    """Describe the outcome of recovering one IDE operation."""

    request_id: str
    sandbox_id: str
    operation: str
    status: str
    docker_status: str | None
    message: str


# ============================================================
# 2. RECOVERY SERVICE
# ============================================================

class SandboxRecoveryService:
    """Reconcile interrupted IDE operations with Docker."""

    IDE_OPERATIONS = {
        "create",
        "start",
        "stop",
        "inspect",
    }
    # Give recovery a temporary lease while inspecting Docker.
    RECOVERY_LEASE_SECONDS = 120

    # Process at most 50 interrupted operations per recovery cycle.
    RECOVERY_BATCH_SIZE = 50
    def __init__(
        self,
        db: Session,
        ide_service: DeveloperIDEService | None = None,
    ):
        """Initialize database and Docker services."""

        self.db = db

        self.ide_service = (
            ide_service
            if ide_service is not None
            else DeveloperIDEService()
        )

    # ========================================================
    # 3. FIND ELIGIBLE OPERATIONS
    # ========================================================

    def find_pending_operations(
        self,
        older_than_minutes: int = 5,
    ):
        """Find old IDE operations whose leases have expired."""

        if (
            isinstance(older_than_minutes, bool)
            or not isinstance(older_than_minutes, int)
            or older_than_minutes < 1
        ):
            raise ValueError(
                "Recovery age must be a positive integer."
            )

        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=older_than_minutes
        )

        statement = text(
            """
            SELECT
                request_id,
                sandbox_id,
                operation,
                created_at
            FROM runner.sandbox_operation_requests
            WHERE status = 'pending'
              AND operation IN (
                  'create',
                  'start',
                  'stop',
                  'inspect'
              )
              AND created_at < :cutoff

              -- Never recover an operation with an active lease.
              AND (
                  lease_expires_at IS NULL
                  OR lease_expires_at <= NOW()
              )

           ORDER BY created_at, request_id
            LIMIT :batch_size
            """
        )

        try:
            rows = self.db.execute(
                statement,
            {
                "cutoff": cutoff,
                "batch_size": self.RECOVERY_BATCH_SIZE,
            },
        ).mappings().all()

            return list(rows)

        finally:
            # Close the read transaction.
            self.db.rollback()

    # ========================================================
    # 4. INSPECT DOCKER BY CONTAINER NAME
    # ========================================================

    def _inspect_container(self, sandbox_id: UUID):
        """
        Find an IDE container using its deterministic Docker name.

        This also handles CREATE operations where PostgreSQL
        does not yet contain the container ID.
        """

        container_name = f"codeview-{sandbox_id.hex}"

        command = [
            "docker",
            "inspect",
            "--format",
            "{{.Id}}|{{.State.Status}}",
            container_name,
        ]

        result = self.ide_service.executor.execute(command)

        if result.timed_out:
            return None, None, "Docker inspection timed out."

        if result.return_code != 0:
            return (
                None,
                None,
                result.stderr.strip()
                or "Docker could not inspect the container.",
            )

        output = result.stdout.strip()

        parts = output.split("|", 1)

        if len(parts) != 2:
            return (
                None,
                None,
                "Unexpected Docker inspection output.",
            )

        container_id, docker_status = parts

        if (
            len(container_id) != 64
            or any(
                character not in "0123456789abcdef"
                for character in container_id.lower()
            )
        ):
            return (
                None,
                None,
                "Invalid Docker container ID.",
            )

        return container_id, docker_status, None

    # ========================================================
    # 5. CLAIM RECOVERY OWNERSHIP
    # ========================================================

    def _claim_recovery_lease(
        self,
        request_id: UUID,
        older_than_minutes: int,
    ):
        """
        Atomically claim an expired IDE operation for recovery.

        Recovery receives a new owner and generation.
        The original worker can no longer finalize its operation
        using its previous lease credentials.
        """

        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=older_than_minutes
        )

        recovery_owner = uuid4()

        try:
            # First identify the sandbox without taking a lock.
            operation = self.db.execute(
                text(
                    """
                    SELECT
                        sandbox_id
                    FROM runner.sandbox_operation_requests
                    WHERE request_id = :request_id
                      AND status = 'pending'
                      AND operation IN (
                          'create',
                          'start',
                          'stop',
                          'inspect'
                      )
                      AND created_at < :cutoff
                      AND (
                          lease_expires_at IS NULL
                          OR lease_expires_at <= NOW()
                      )
                    """
                ),
                {
                    "request_id": request_id,
                    "cutoff": cutoff,
                },
            ).mappings().one_or_none()

            if operation is None:
                self.db.rollback()
                return None

            sandbox_id = operation["sandbox_id"]

            # Lock sandbox first, matching the IDE handler.
            sandbox = self.db.execute(
                select(SandboxRecord)
                .where(
                    SandboxRecord.sandbox_id == sandbox_id
                )
                .with_for_update(skip_locked=True)
            ).scalar_one_or_none()

            if sandbox is None:
                self.db.rollback()
                return None

            # Claim the operation only if its lease is still expired.
            #
            # The generation increases so the original worker's
            # ownership credentials become invalid.
            claimed = self.db.execute(
                text(
                    """
                    UPDATE runner.sandbox_operation_requests
                    SET
                        lease_owner = :recovery_owner,
                        lease_generation = lease_generation + 1,
                        lease_expires_at = (
                            NOW()
                            + (:lease_seconds * INTERVAL '1 second')
                        )
                    WHERE request_id = :request_id
                      AND sandbox_id = :sandbox_id
                      AND status = 'pending'
                      AND operation IN (
                          'create',
                          'start',
                          'stop',
                          'inspect'
                      )
                      AND created_at < :cutoff
                      AND (
                          lease_expires_at IS NULL
                          OR lease_expires_at <= NOW()
                      )
                    RETURNING
                        request_id,
                        sandbox_id,
                        operation,
                        lease_owner,
                        lease_generation
                    """
                ),
                {
                    "request_id": request_id,
                    "sandbox_id": sandbox_id,
                    "cutoff": cutoff,
                    "recovery_owner": recovery_owner,
                    "lease_seconds": self.RECOVERY_LEASE_SECONDS,
                },
            ).mappings().one_or_none()

            if claimed is None:
                self.db.rollback()
                return None

            # Save the recovery lease before inspecting Docker.
            self.db.commit()

            return dict(claimed)

        except Exception:
            self.db.rollback()
            raise

    # ========================================================
    # 6. FINISH AN OPERATION USING RECOVERY OWNERSHIP
    # ========================================================

    def _finish_operation(
        self,
        request_id: UUID,
        status: str,
        lease_owner: UUID,
        lease_generation: int,
        error_message: str | None = None,
    ):
        """Finalize an operation only while recovery owns its lease."""

        if status not in {"completed", "failed"}:
            raise ValueError(
                "Invalid recovery result status."
            )

        result = self.db.execute(
            text(
                """
                UPDATE runner.sandbox_operation_requests
                SET
                    status = :status,
                    completed_at = NOW(),
                    error_message = :error_message
                WHERE request_id = :request_id
                  AND status = 'pending'
                  AND lease_owner = :lease_owner
                  AND lease_generation = :lease_generation
                  AND lease_expires_at > NOW()
                """
            ),
            {
                "request_id": request_id,
                "status": status,
                "lease_owner": lease_owner,
                "lease_generation": lease_generation,
                "error_message": error_message,
            },
        )

        if result.rowcount != 1:
            raise RuntimeError(
                "Recovery cannot finalize the operation: "
                "lease expired or ownership was lost."
            )

    # ========================================================
    # 7. RECOVER ONE OPERATION
    # ========================================================

    def recover_operation(
        self,
        request_id: UUID,
        older_than_minutes: int = 5,
    ) -> RecoveryResult:
        """Inspect Docker and reconcile one interrupted operation."""

        if not isinstance(request_id, UUID):
            raise ValueError(
                "Invalid recovery request ID."
            )

        if (
            isinstance(older_than_minutes, bool)
            or not isinstance(older_than_minutes, int)
            or older_than_minutes < 1
        ):
            raise ValueError(
                "Invalid recovery age."
            )

        # ----------------------------------------------------
        # STEP 1: CLAIM THE EXPIRED OPERATION
        # ----------------------------------------------------

        claimed = self._claim_recovery_lease(
            request_id=request_id,
            older_than_minutes=older_than_minutes,
        )

        if claimed is None:
            return RecoveryResult(
                request_id=str(request_id),
                sandbox_id="",
                operation="unknown",
                status="skipped",
                docker_status=None,
                message=(
                    "Operation is not eligible for recovery, "
                    "has an active lease, or is locked."
                ),
            )

        sandbox_id = claimed["sandbox_id"]
        operation_name = claimed["operation"]
        lease_owner = claimed["lease_owner"]
        lease_generation = claimed["lease_generation"]

        # ----------------------------------------------------
        # STEP 2: INSPECT DOCKER AND PREPARE LEASE HANDOFF
        # ----------------------------------------------------
        # Create the heartbeat for this recovery operation.
        #
        # It uses the lease ownership information
        # returned when we claimed the operation.
        heartbeat = IDELeaseHeartbeat(
            request_id=request_id,
            lease_owner=lease_owner,
            lease_generation=lease_generation,
        )

        container_id = None
        docker_status = None
        error = None

        # Track whether the lease handoff succeeded.
        handoff_successful = False
        heartbeat_started = False

        try:
            # Start the background heartbeat.
            heartbeat.start()

            # Record successful startup.
            heartbeat_started = True
            try:
                # Check the container's actual Docker state.
                container_id, docker_status, error = (
                    self._inspect_container(sandbox_id)
                )

            except Exception as inspection_error:
                # Never assume Docker succeeded after an exception.
                error = str(inspection_error)

        except Exception as heartbeat_error:
            error = f"Recovery heartbeat failed: {heartbeat_error}"

        finally:
            if heartbeat_started:
                # Stop the heartbeat and renew the lease.
                handoff_successful = heartbeat.stop_and_renew()

            else:
                # Do not renew a lease if startup failed.
                heartbeat.stop()
                
            # Never finalize without a successful lease handoff.
        if not handoff_successful or heartbeat.lost_lease:

            # Discard any uncommitted database changes.
            self.db.rollback()

            # A heartbeat startup failure means recovery
            # could not proceed after claiming the operation.
            if not heartbeat_started:
                recovery_status = "unresolved"

            else:
                # A failed handoff or lost lease means
                # ownership is no longer safe to assume.
                recovery_status = "skipped"

            return RecoveryResult(
                request_id=str(request_id),
                sandbox_id=str(sandbox_id),
                operation=operation_name,
                status=recovery_status,
                docker_status=docker_status,
                message=(
                    "Recovery lease handoff failed "
                    "or ownership was lost."
                ),
            )

        if error:
            # Do not guess Docker's state after an inspection error.
            # Leave the operation pending for later recovery.
            return RecoveryResult(
                request_id=str(request_id),
                sandbox_id=str(sandbox_id),
                operation=operation_name,
                status="unresolved",
                docker_status=None,
                message=error,
            )

        # ----------------------------------------------------
        # STEP 3: RECHECK OWNERSHIP AND LOCK THE SANDBOX
        # ----------------------------------------------------

        try:
            # Keep lock order consistent with the IDE handler.
            sandbox = self.db.execute(
                select(SandboxRecord)
                .where(
                    SandboxRecord.sandbox_id == sandbox_id
                )
                .with_for_update()
            ).scalar_one_or_none()

            if sandbox is None:
                self.db.rollback()

                return RecoveryResult(
                    request_id=str(request_id),
                    sandbox_id=str(sandbox_id),
                    operation=operation_name,
                    status="unresolved",
                    docker_status=docker_status,
                    message="Sandbox database record is missing.",
                )

            # Verify recovery still owns the operation.
            ownership = self.db.execute(
                text(
                    """
                    SELECT request_id
                    FROM runner.sandbox_operation_requests
                    WHERE request_id = :request_id
                      AND sandbox_id = :sandbox_id
                      AND status = 'pending'
                      AND lease_owner = :lease_owner
                      AND lease_generation = :lease_generation
                      AND lease_expires_at > NOW()
                    FOR UPDATE
                    """
                ),
                {
                    "request_id": request_id,
                    "sandbox_id": sandbox_id,
                    "lease_owner": lease_owner,
                    "lease_generation": lease_generation,
                },
            ).one_or_none()

            if ownership is None:
                self.db.rollback()

                return RecoveryResult(
                    request_id=str(request_id),
                    sandbox_id=str(sandbox_id),
                    operation=operation_name,
                    status="skipped",
                    docker_status=docker_status,
                    message=(
                        "Recovery lease expired or ownership was lost."
                    ),
                )

            # Confirm the container identity.
            if (
                sandbox.container_id
                and sandbox.container_id != container_id
            ):
                self.db.rollback()

                return RecoveryResult(
                    request_id=str(request_id),
                    sandbox_id=str(sandbox_id),
                    operation=operation_name,
                    status="unresolved",
                    docker_status=docker_status,
                    message="Docker container identity mismatch.",
                )

            # ------------------------------------------------
            # STEP 4: RECONCILE THE OPERATION
            # ------------------------------------------------

            completed = False
            message = ""

            if operation_name == "create":

                if docker_status in {
                    "created",
                    "running",
                    "exited",
                }:
                    sandbox.container_id = container_id

                    if docker_status == "running":
                        sandbox.status = "running"

                    completed = True
                    message = "IDE container creation recovered."

            elif operation_name == "start":

                if docker_status == "running":
                    sandbox.status = "running"

                    completed = True
                    message = "IDE startup recovered."

            elif operation_name == "stop":

                if docker_status == "exited":
                    sandbox.status = "stopped"

                    completed = True
                    message = "IDE shutdown recovered."

            elif operation_name == "inspect":

                # Inspection does not change the sandbox lifecycle.
                completed = True
                message = "IDE inspection recovered."

            # ------------------------------------------------
            # STEP 5: SAVE THE CONFIRMED RESULT
            # ------------------------------------------------

            if completed:
                self._finish_operation(
                    request_id=request_id,
                    status="completed",
                    lease_owner=lease_owner,
                    lease_generation=lease_generation,
                )

                # Commit sandbox and operation changes together.
                self.db.commit()

                return RecoveryResult(
                    request_id=str(request_id),
                    sandbox_id=str(sandbox_id),
                    operation=operation_name,
                    status="completed",
                    docker_status=docker_status,
                    message=message,
                )

            # Docker does not confirm the requested operation.
            # Leave it pending for further investigation.
            self.db.rollback()

            return RecoveryResult(
                request_id=str(request_id),
                sandbox_id=str(sandbox_id),
                operation=operation_name,
                status="unresolved",
                docker_status=docker_status,
                message=(
                    "Docker state does not confirm the "
                    "requested operation. Manual review required."
                ),
            )

        except Exception:
            self.db.rollback()
            raise

    # ========================================================
    # 8. RECOVER ALL ELIGIBLE OPERATIONS
    # ========================================================

    def recover_pending_operations(
        self,
        older_than_minutes: int = 5,
    ) -> list[RecoveryResult]:
        """Attempt recovery for every eligible IDE operation."""

        pending_operations = self.find_pending_operations(
            older_than_minutes=older_than_minutes
        )

        results = []

        for operation in pending_operations:
            request_id = operation["request_id"]

            try:
                result = self.recover_operation(
                    request_id=request_id,
                    older_than_minutes=older_than_minutes,
                )

            except Exception as error:
                # Continue processing other operations.
                result = RecoveryResult(
                    request_id=str(request_id),
                    sandbox_id=str(operation["sandbox_id"]),
                    operation=operation["operation"],
                    status="unresolved",
                    docker_status=None,
                    message=str(error),
                )

            results.append(result)

        return results