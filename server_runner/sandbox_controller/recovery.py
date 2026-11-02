"""Recover interrupted persistent Docker IDE operations."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from server_runner.runner_service.models import SandboxRecord

from server_runner.sandbox_controller.developer_ide import (
    DeveloperIDEService,
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
    """Reconcile unfinished IDE operations with Docker."""

    # Only persistent IDE operations are recoverable here.
    IDE_OPERATIONS = {
        "create",
        "start",
        "stop",
        "inspect",
    }

    def __init__(
        self,
        db: Session,
        ide_service: DeveloperIDEService | None = None,
    ):
        """Reuse the existing database and Docker services."""

        self.db = db

        self.ide_service = (
            ide_service
            if ide_service is not None
            else DeveloperIDEService()
        )

    # ========================================================
    # 3. FIND UNFINISHED OPERATIONS
    # ========================================================

    def find_pending_operations(
        self,
        older_than_minutes: int = 5,
    ):
        """Find old pending IDE operations, excluding RUN."""

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
            ORDER BY created_at, request_id
            """
        )

        rows = self.db.execute(
            statement,
            {"cutoff": cutoff},
        ).mappings().all()

        # Finish the read transaction before recovery begins.
        self.db.rollback()

        return list(rows)

    # ========================================================
    # 4. INSPECT DOCKER BY CONTAINER NAME
    # ========================================================

    def _inspect_container(self, sandbox_id: UUID):
        """
        Find a container using CodeView's deterministic name.

        This handles CREATE operations where PostgreSQL has
        not yet received the Docker container ID.
        """

        # Match the naming convention in DockerRuntime.
        container_name = f"codeview-{sandbox_id.hex}"

        # The name comes from a validated UUID, not user text.
        command = [
            "docker",
            "inspect",
            "--format",
            "{{.Id}}|{{.State.Status}}",
            container_name,
        ]

        # Reuse the existing trusted command executor.
        result = self.ide_service.executor.execute(command)

        if result.timed_out:
            return None, None, "Docker inspection timed out."

        if result.return_code != 0:
            # A failed inspect is not proof that a container
            # never existed. Docker itself might be unavailable.
            return (
                None,
                None,
                result.stderr.strip()
                or "Docker could not inspect the container.",
            )

        output = result.stdout.strip()

        parts = output.split("|", 1)

        if len(parts) != 2:
            return None, None, "Unexpected Docker inspection output."

        container_id, docker_status = parts

        # Validate Docker's returned container ID.
        if (
            len(container_id) != 64
            or any(
                character not in "0123456789abcdef"
                for character in container_id.lower()
            )
        ):
            return None, None, "Invalid Docker container ID."

        return container_id, docker_status, None

    # ========================================================
    # 5. UPDATE AN OPERATION
    # ========================================================

    def _finish_operation(
        self,
        request_id: UUID,
        status: str,
        error_message: str | None = None,
    ):
        """Save a completed or failed operation."""

        if status not in {"completed", "failed"}:
            raise ValueError("Invalid recovery result status.")

        self.db.execute(
            text(
                """
                UPDATE runner.sandbox_operation_requests
                SET
                    status = :status,
                    completed_at = NOW(),
                    error_message = :error_message
                WHERE request_id = :request_id
                  AND status = 'pending'
                """
            ),
            {
                "request_id": request_id,
                "status": status,
                "error_message": error_message,
            },
        )

    # ========================================================
    # 6. RECOVER ONE OPERATION
    # ========================================================

    def recover_operation(
        self,
        request_id: UUID,
        older_than_minutes: int = 5,
    ) -> RecoveryResult:
        """Inspect Docker and reconcile one interrupted operation."""

        if not isinstance(request_id, UUID):
            raise ValueError("Invalid recovery request ID.")

        if (
            isinstance(older_than_minutes, bool)
            or not isinstance(older_than_minutes, int)
            or older_than_minutes < 1
        ):
            raise ValueError("Invalid recovery age.")

        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=older_than_minutes
        )

        try:
            # ------------------------------------------------
            # STEP 1: LOCK THE OPERATION RECORD
            # ------------------------------------------------

            operation = self.db.execute(
                text(
                    """
                    SELECT
                        request_id,
                        sandbox_id,
                        operation,
                        status
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
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {
                    "request_id": request_id,
                    "cutoff": cutoff,
                },
            ).mappings().one_or_none()

            if operation is None:
                self.db.rollback()

                return RecoveryResult(
                    request_id=str(request_id),
                    sandbox_id="",
                    operation="unknown",
                    status="skipped",
                    docker_status=None,
                    message=(
                        "Operation is not eligible for recovery "
                        "or is locked by another recovery worker."
                    ),
                )

            sandbox_id = operation["sandbox_id"]
            operation_name = operation["operation"]

            # ------------------------------------------------
            # STEP 2: LOCK THE SANDBOX
            # ------------------------------------------------

            sandbox = self.db.execute(
                select(SandboxRecord)
                .where(
                    SandboxRecord.sandbox_id == sandbox_id
                )
                .with_for_update()
            ).scalar_one_or_none()

            if sandbox is None:
                # Missing sandbox records require investigation.
                # Do not guess the correct lifecycle.
                self.db.rollback()

                return RecoveryResult(
                    request_id=str(request_id),
                    sandbox_id=str(sandbox_id),
                    operation=operation_name,
                    status="unresolved",
                    docker_status=None,
                    message="Sandbox database record is missing.",
                )

            # ------------------------------------------------
            # STEP 3: INSPECT THE ACTUAL DOCKER CONTAINER
            # ------------------------------------------------

            container_id, docker_status, error = (
                self._inspect_container(sandbox_id)
            )

            if error:
                # Leave the operation pending if Docker's
                # actual state cannot be established.
                self.db.rollback()

                return RecoveryResult(
                    request_id=str(request_id),
                    sandbox_id=str(sandbox_id),
                    operation=operation_name,
                    status="unresolved",
                    docker_status=None,
                    message=error,
                )

            # A saved container ID must match the container
            # found using CodeView's deterministic name.
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
                    # Recover the ID even if the original
                    # CREATE request crashed before saving it.
                    sandbox.container_id = container_id

                    if docker_status == "running":
                        sandbox.status = "running"

                    completed = True
                    message = "IDE container creation recovered."

            elif operation_name == "start":

                if docker_status == "running":
                    # Docker confirms that startup succeeded.
                    sandbox.status = "running"

                    completed = True
                    message = "IDE startup recovered."

            elif operation_name == "stop":

                if docker_status == "exited":
                    # Docker confirms that shutdown succeeded.
                    sandbox.status = "stopped"

                    completed = True
                    message = "IDE shutdown recovered."

            elif operation_name == "inspect":

                # Inspection is read-only.
                # It does not change the sandbox lifecycle.
                completed = True
                message = "IDE inspection recovered."

            # ------------------------------------------------
            # STEP 5: SAVE THE CONFIRMED RESULT
            # ------------------------------------------------

            if completed:
                self._finish_operation(
                    request_id=request_id,
                    status="completed",
                )

                self.db.commit()

                return RecoveryResult(
                    request_id=str(request_id),
                    sandbox_id=str(sandbox_id),
                    operation=operation_name,
                    status="completed",
                    docker_status=docker_status,
                    message=message,
                )

            # Docker's state does not prove that the requested
            # operation completed. Leave it pending.
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
    # 7. RECOVER ALL ELIGIBLE OPERATIONS
    # ========================================================

    def recover_pending_operations(
        self,
        older_than_minutes: int = 5,
    ) -> list[RecoveryResult]:
        """Attempt recovery for all eligible IDE operations."""

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
                # One failed recovery must not prevent the
                # remaining operations from being inspected.
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