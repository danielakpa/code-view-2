"""Internal FastAPI application for the CodeView sandbox controller."""

import traceback

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
)

from pydantic import ValidationError

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

# ============================================================
# 1. AUTHENTICATION AND DATABASE
# ============================================================

from server_runner.sandbox_controller.authentication import (
    verify_controller_token,
)

from server_runner.runner_service.database import (
    get_db,
)

# ============================================================
# 2. CONTROLLER SERVICES
# ============================================================

from server_runner.sandbox_controller.service import (
    SandboxControllerService,
)

from server_runner.sandbox_controller.operation_handler import (
    SandboxOperationHandler,
)

from server_runner.sandbox_controller.operation import (
    SandboxOperation,
    SandboxOperationRequest,
)

# ============================================================
# 3. EXISTING CODE EXECUTION
# ============================================================

from server_runner.sandbox_controller.execution_request import (
    ControllerExecutionRequest,
)

from server_runner.sandbox_controller.code_execution_pipeline import (
    CodeExecutionPipeline,
)

from server_runner.sandbox_controller.replay import (
    ReplayProtectionError,
)


# ============================================================
# 4. CREATE FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="CodeView Sandbox Controller",

    # Disable public API documentation.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


# Initialize the existing Controller service.
controller = SandboxControllerService()


# ============================================================
# 5. HEALTH CHECK
# ============================================================

@app.get("/health")
def health_check():
    """Return the Controller's health information."""

    return controller.health_check()


# ============================================================
# 6. SECURITY SUMMARY
# ============================================================

@app.get("/internal/security")
def security_summary(
    authenticated: bool = Depends(verify_controller_token),
):
    """Return non-secret security settings."""

    return controller.get_security_summary()


# ============================================================
# 7. IDE OPERATION ENDPOINT
# ============================================================

@app.post("/internal/ide/operations")
async def ide_operation(
    http_request: Request,

    # Only authenticated internal services can call this endpoint.
    authenticated: bool = Depends(verify_controller_token),

    # Provide the database session.
    db: Session = Depends(get_db),
):
    """
    Validate and record a developer IDE operation.

    Supported operations:
        CREATE
        START
        STOP
        INSPECT

    IMPORTANT:
    This endpoint does not execute Docker commands yet.
    """

    # --------------------------------------------------------
    # STEP 1: VALIDATE THE REQUEST BODY
    # --------------------------------------------------------

    try:
        # Read the JSON request.
        request_body = await http_request.body()

        # Validate the sandbox operation.
        operation_request = (
            SandboxOperationRequest.model_validate_json(
                request_body
            )
        )

    except ValidationError as error:

        raise HTTPException(
            status_code=422,
            detail="Invalid IDE operation request.",
        ) from error

    # --------------------------------------------------------
    # STEP 2: REJECT CODE EXECUTION OPERATIONS
    # --------------------------------------------------------

    # The IDE endpoint manages containers.
    # Code execution uses the separate executions endpoint.
    if operation_request.operation not in {
        SandboxOperation.CREATE,
        SandboxOperation.START,
        SandboxOperation.STOP,
        SandboxOperation.INSPECT,
    }:
        raise HTTPException(
            status_code=422,
            detail="Unsupported IDE operation.",
        )

    # --------------------------------------------------------
    # STEP 3: AUTHORIZE AND RECORD THE OPERATION
    # --------------------------------------------------------

    try:
        # Use the existing PostgreSQL-backed operation handler.
        handler = SandboxOperationHandler(db)

        # Verify sandbox ownership, lifecycle, and replay protection.
        result = handler.handle(operation_request)

        # Return the recorded operation.
        return result

    except ReplayProtectionError as error:

        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except PermissionError as error:

        raise HTTPException(
            status_code=403,
            detail=str(error),
        ) from error

    except ValueError as error:

        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except SQLAlchemyError as error:

        raise HTTPException(
            status_code=503,
            detail="Sandbox authorization database unavailable.",
        ) from error


# ============================================================
# 8. EXISTING CODE EXECUTION ENDPOINT
# ============================================================

@app.post("/internal/executions")
async def execute_code(
    http_request: Request,

    # Authenticate the calling service.
    authenticated: bool = Depends(verify_controller_token),

    # Provide the database session.
    db: Session = Depends(get_db),
):
    """
    Authorize and execute code in a temporary Docker container.

    Existing CodeView functionality is preserved.
    """

    # --------------------------------------------------------
    # STEP 1: VALIDATE THE EXECUTION REQUEST
    # --------------------------------------------------------

    try:
        request_body = await http_request.body()

        execution_request = (
            ControllerExecutionRequest.model_validate_json(
                request_body
            )
        )

    except ValidationError as error:

        raise HTTPException(
            status_code=422,
            detail="Invalid code execution request.",
        ) from error

    # --------------------------------------------------------
    # STEP 2: AUTHORIZE AND RECORD THE OPERATION
    # --------------------------------------------------------

    try:
        operation_handler = SandboxOperationHandler(db)

        operation_handler.handle(
            execution_request.operation_request
        )

    except ReplayProtectionError as error:

        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except PermissionError as error:

        raise HTTPException(
            status_code=403,
            detail=str(error),
        ) from error

    except ValueError as error:

        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except SQLAlchemyError as error:

        traceback.print_exc()

        raise HTTPException(
            status_code=503,
            detail="Sandbox authorization database unavailable.",
        ) from error

    # --------------------------------------------------------
    # STEP 3: EXECUTE THE SOURCE CODE
    # --------------------------------------------------------

    try:
        pipeline = CodeExecutionPipeline()

        result = pipeline.execute(
            sandbox_id=execution_request.operation_request.sandbox_id,
            language=execution_request.language,
            source=execution_request.source,
        )

        return result

    except ValueError as error:

        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

    except RuntimeError as error:

        traceback.print_exc()

        raise HTTPException(
            status_code=503,
            detail="Code execution service unavailable.",
        ) from error