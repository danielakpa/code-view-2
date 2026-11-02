"""Coordinate sandbox operations and PostgreSQL lifecycle changes."""

# ============================================================
# 1. DATABASE IMPORTS
# ============================================================

# Update operation-tracking records in PostgreSQL.
from sqlalchemy import text

# Reuse the existing Docker IDE service.
from server_runner.sandbox_controller.developer_ide import (
    DeveloperIDEService,
)

# Build database queries.
from sqlalchemy import select

# Manage PostgreSQL database sessions.
from sqlalchemy.orm import Session


# ============================================================
# 2. CODEVIEW IMPORTS
# ============================================================

# Database model representing a developer's sandbox.
from server_runner.runner_service.models import SandboxRecord

# Represent trusted sandbox ownership information.
from server_runner.sandbox_controller.authorization import (
    SandboxIdentity,
)

# Store operation request IDs in PostgreSQL for replay protection.
from server_runner.sandbox_controller.database_replay import (
    DatabaseReplayStore,
)

# Enforce valid sandbox lifecycle transitions.
from server_runner.sandbox_controller.lifecycle import (
    SandboxLifecycle,
)

# Define supported sandbox operations and request validation.
from server_runner.sandbox_controller.operation import (
    SandboxOperation,
    SandboxOperationRequest,
)

# Validate operations and claim request IDs.
from server_runner.sandbox_controller.operation_service import (
    SandboxOperationService,
)


# ============================================================
# 3. SANDBOX OPERATION HANDLER
# ============================================================

class SandboxOperationHandler:
    """Authorize, validate, and record sandbox operations atomically."""

    def __init__(self, db: Session):
        """Initialize the handler with a PostgreSQL database session."""

        # Store the database session.
        self.db = db

        # Use PostgreSQL to prevent replayed operation requests.
        self.operation_service = SandboxOperationService(
            replay_store=DatabaseReplayStore(db)
        )

        # Reuse CodeView's existing sandbox lifecycle rules.
        self.lifecycle = SandboxLifecycle()

    def handle(self, request: SandboxOperationRequest):
        """
        Validate and record an authorized sandbox operation.

        This method does not execute Docker commands.

        SECURITY:
        Developer and group IDs supplied in a request are not proof
        of authentication. The calling service must be authenticated.
        """

        # ----------------------------------------------------
        # STEP 1: VALIDATE THE REQUEST TYPE
        # ----------------------------------------------------

        if not isinstance(request, SandboxOperationRequest):
            raise ValueError(
                "Invalid sandbox operation request."
            )

        try:
            # ------------------------------------------------
            # STEP 2: FIND AND LOCK THE SANDBOX
            # ------------------------------------------------

            # Lock the matching database row until the transaction
            # finishes. This helps prevent concurrent operations
            # from changing the sandbox at the same time.
            statement = (
                select(SandboxRecord)
                .where(
                    SandboxRecord.sandbox_id == request.sandbox_id,
                    SandboxRecord.developer_id == request.developer_id,
                    SandboxRecord.group_id == request.group_id,
                )
                .with_for_update()
            )

            sandbox = self.db.execute(
                statement
            ).scalar_one_or_none()

            # Reject missing sandboxes and ownership mismatches.
            if sandbox is None:
                raise PermissionError(
                    "Sandbox not found or access denied."
                )

            # ------------------------------------------------
            # STEP 3: BUILD TRUSTED SANDBOX IDENTITY
            # ------------------------------------------------

            # Use ownership information retrieved from PostgreSQL.
            trusted_identity = SandboxIdentity(
                sandbox_id=sandbox.sandbox_id,
                developer_id=sandbox.developer_id,
                group_id=sandbox.group_id,
            )

            # ------------------------------------------------
            # STEP 4: CHECK THE REQUESTED OPERATION
            # ------------------------------------------------

            if request.operation == SandboxOperation.START:

                # Ask the lifecycle system to validate starting.
                new_status = self.lifecycle.transition(
                    sandbox.status,
                    "starting",
                )

            elif request.operation == SandboxOperation.STOP:

                # Ask the lifecycle system to validate stopping.
                new_status = self.lifecycle.transition(
                    sandbox.status,
                    "stopped",
                )

            elif request.operation == SandboxOperation.INSPECT:

                # Inspection does not change sandbox status.
                new_status = sandbox.status

            elif request.operation == SandboxOperation.RUN:

                # Code execution uses a separate temporary
                # container rather than the main sandbox.

                allowed_statuses = {
                    "prepared",
                    "running",
                    "stopped",
                }

                # Reject sandboxes that are not ready.
                if sandbox.status not in allowed_statuses:
                    raise ValueError(
                        "Sandbox is not ready for code execution."
                    )

                # Temporary execution does not change status.
                new_status = sandbox.status

            elif request.operation == SandboxOperation.CREATE:

                # Sandbox creation is handled separately.
                # Reject attempts to recreate an active sandbox.
                if sandbox.status != "prepared":
                    raise ValueError(
                        "Sandbox is already prepared or active."
                    )

                new_status = sandbox.status

            else:
                raise ValueError(
                    "Unsupported sandbox operation."
                )

            # ------------------------------------------------
            # STEP 5: AUTHORIZE AND CLAIM THE REQUEST ID
            # ------------------------------------------------

            # Verify the operation against the trusted identity.
            #
            # The replay store records the request ID to prevent
            # the same operation from being processed again.
            self.operation_service.validate_and_claim(
                request=request,
                trusted_identity=trusted_identity,
            )

            # ------------------------------------------------
            # STEP 6: SAVE THE LIFECYCLE CHANGE
            # ------------------------------------------------

            # Update the sandbox status.
            sandbox.status = new_status

            # Commit the operation claim and lifecycle update
            # in the same PostgreSQL transaction.
            self.db.commit()

            # ------------------------------------------------
            # STEP 7: RETURN THE OPERATION RESULT
            # ------------------------------------------------

            return {
                "request_id": str(request.request_id),
                "sandbox_id": str(request.sandbox_id),
                "operation": request.operation.value,
                "status": "recorded",
                "sandbox_status": sandbox.status,
            }

        except Exception:

            # Undo all database changes if any operation fails.
            #
            # This includes unsuccessful lifecycle transitions
            # and replay-protection claims.
            self.db.rollback()

            raise


    # ========================================================
    # 4. PERSISTENT DOCKER IDE OPERATIONS
    # ========================================================

    def _update_ide_operation(
        self,
        request_id,
        status,
        error_message=None,
    ):
        """Save the result of an IDE operation in PostgreSQL."""

        self.db.execute(
            text(
                """
                UPDATE runner.sandbox_operation_requests
                SET
                    status = :status,
                    completed_at = CASE
                        WHEN :status = 'pending' THEN NULL
                        ELSE NOW()
                    END,
                    error_message = :error_message
                WHERE request_id = :request_id
                """
            ),
            {
                "request_id": request_id,
                "status": status,
                "error_message": error_message,
            },
        )

    def handle_ide_operation(
        self,
        request: SandboxOperationRequest,
    ):
        """Execute and track an authorized Docker IDE operation."""

        # ----------------------------------------------------
        # STEP 1: VALIDATE THE REQUEST
        # ----------------------------------------------------

        if not isinstance(request, SandboxOperationRequest):
            raise ValueError("Invalid IDE operation request.")

        # Temporary code execution is handled separately.
        if request.operation == SandboxOperation.RUN:
            raise ValueError(
                "RUN is not a persistent IDE operation."
            )

        # ----------------------------------------------------
        # STEP 2: AUTHORIZE AND RESERVE THE OPERATION
        # ----------------------------------------------------

        try:
            # Lock the developer's sandbox database record.
            statement = (
                select(SandboxRecord)
                .where(
                    SandboxRecord.sandbox_id == request.sandbox_id,
                    SandboxRecord.developer_id == request.developer_id,
                    SandboxRecord.group_id == request.group_id,
                )
                .with_for_update()
            )

            sandbox = self.db.execute(
                statement
            ).scalar_one_or_none()

            if sandbox is None:
                raise PermissionError(
                    "Sandbox not found or access denied."
                )

            # Build identity from trusted database information.
            trusted_identity = SandboxIdentity(
                sandbox_id=sandbox.sandbox_id,
                developer_id=sandbox.developer_id,
                group_id=sandbox.group_id,
            )

            # Check the operation before claiming it.
            if request.operation == SandboxOperation.CREATE:

                if sandbox.status != "prepared":
                    raise ValueError(
                        "Sandbox is not ready for IDE creation."
                    )

                if sandbox.container_id:
                    raise ValueError(
                        "IDE container already exists."
                    )

            elif request.operation == SandboxOperation.START:

                if not sandbox.container_id:
                    raise ValueError(
                        "Create the IDE before starting it."
                    )

                # Reuse existing lifecycle validation.
                self.lifecycle.transition(
                    sandbox.status,
                    "starting",
                )

            elif request.operation == SandboxOperation.STOP:

                if not sandbox.container_id:
                    raise ValueError(
                        "IDE container does not exist."
                    )

                self.lifecycle.transition(
                    sandbox.status,
                    "stopped",
                )

            elif request.operation == SandboxOperation.INSPECT:

                if not sandbox.container_id:
                    raise ValueError(
                        "IDE container does not exist."
                    )

            else:
                raise ValueError(
                    "Unsupported IDE operation."
                )

            # Check for another unfinished IDE operation.
            #
            # The sandbox row lock serializes this check
            # with other requests using the same handler.
            pending_count = self.db.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM runner.sandbox_operation_requests
                    WHERE sandbox_id = :sandbox_id
                      AND status = 'pending'
                    """
                ),
                {"sandbox_id": request.sandbox_id},
            ).scalar()

            if (
                isinstance(pending_count, int)
                and pending_count > 0
            ):
                raise ValueError(
                    "Another sandbox operation is pending."
                )

            # Reuse existing authorization and replay protection.
            self.operation_service.validate_and_claim(
                request=request,
                trusted_identity=trusted_identity,
            )

            # START enters its transitional state.
            if request.operation == SandboxOperation.START:
                sandbox.status = "starting"

            # The replay store inserts the operation record.
            # Its default status is 'pending'.
            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        # ----------------------------------------------------
        # STEP 3: EXECUTE DOCKER
        # ----------------------------------------------------

        ide_service = DeveloperIDEService()

        container_id = sandbox.container_id
        docker_status = None
        error_message = None
        success = False

        try:
            if request.operation == SandboxOperation.CREATE:

                result = ide_service.create_ide(
                    trusted_identity
                )

                if result.success:
                    # Docker create returns the new container ID.
                    container_id = result.output.strip()

                    # Validate the returned Docker identifier.
                    if (
                        len(container_id) != 64
                        or any(
                            character not in "0123456789abcdef"
                            for character in container_id.lower()
                        )
                    ):
                        raise RuntimeError(
                            "Docker returned an invalid container ID."
                        )

                    # Confirm that Docker recognizes the container.
                    inspection = ide_service.inspect_ide(
                        container_id
                    )

                    docker_status = inspection.output.strip()

                    success = (
                        inspection.success
                        and docker_status == "created"
                    )

                    if not success:
                        error_message = (
                            inspection.error
                            or "Docker did not confirm IDE creation."
                        )

                else:
                    error_message = (
                        result.error or "IDE creation failed."
                    )

            elif request.operation == SandboxOperation.START:

                result = ide_service.start_ide(
                    container_id
                )

                if result.success:
                    inspection = ide_service.inspect_ide(
                        container_id
                    )

                    docker_status = inspection.output.strip()

                    success = (
                        inspection.success
                        and docker_status == "running"
                    )

                    if not success:
                        error_message = (
                            inspection.error
                            or "Docker did not confirm IDE startup."
                        )

                else:
                    error_message = (
                        result.error or "IDE startup failed."
                    )

            elif request.operation == SandboxOperation.STOP:

                result = ide_service.stop_ide(
                    container_id
                )

                if result.success:
                    inspection = ide_service.inspect_ide(
                        container_id
                    )

                    docker_status = inspection.output.strip()

                    success = (
                        inspection.success
                        and docker_status == "exited"
                    )

                    if not success:
                        error_message = (
                            inspection.error
                            or "Docker did not confirm IDE shutdown."
                        )

                else:
                    error_message = (
                        result.error or "IDE shutdown failed."
                    )

            elif request.operation == SandboxOperation.INSPECT:

                inspection = ide_service.inspect_ide(
                    container_id
                )

                docker_status = inspection.output.strip()

                success = inspection.success

                if not success:
                    error_message = (
                        inspection.error or "IDE inspection failed."
                    )

        except Exception as error:
            error_message = str(error)
            success = False

        # ----------------------------------------------------
        # STEP 4: SAVE THE CONFIRMED RESULT
        # ----------------------------------------------------

        try:
            # Lock the sandbox again before updating its state.
            statement = (
                select(SandboxRecord)
                .where(
                    SandboxRecord.sandbox_id == request.sandbox_id,
                    SandboxRecord.developer_id == request.developer_id,
                    SandboxRecord.group_id == request.group_id,
                )
                .with_for_update()
            )

            sandbox = self.db.execute(
                statement
            ).scalar_one_or_none()

            if sandbox is None:
                raise RuntimeError(
                    "Sandbox disappeared during IDE operation."
                )

            if success:

                if request.operation == SandboxOperation.CREATE:
                    sandbox.container_id = container_id

                elif request.operation == SandboxOperation.START:
                    sandbox.status = self.lifecycle.transition(
                        sandbox.status,
                        "running",
                    )

                elif request.operation == SandboxOperation.STOP:
                    sandbox.status = "stopped"

                # INSPECT does not modify the lifecycle.

            elif request.operation == SandboxOperation.START:
                # A failed startup must never appear as running.
                sandbox.status = "failed"

            # Save the operation's final result.
            self._update_ide_operation(
                request_id=request.request_id,
                status="completed" if success else "failed",
                error_message=error_message,
            )

            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        # ----------------------------------------------------
        # STEP 5: RETURN THE RESULT
        # ----------------------------------------------------

        return {
            "request_id": str(request.request_id),
            "sandbox_id": str(request.sandbox_id),
            "operation": request.operation.value,
            "status": "completed" if success else "failed",
            "sandbox_status": sandbox.status,
            "docker_status": docker_status,
            "error": error_message,
        }