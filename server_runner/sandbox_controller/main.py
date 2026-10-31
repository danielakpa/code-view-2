"""Internal FastAPI application for the CodeView sandbox controller."""

# Print complete error details during debugging.
import traceback

# ============================================================
# 1. IMPORTS
# ============================================================

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
)

from pydantic import ValidationError

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

# Authentication for internal Controller requests.
from server_runner.sandbox_controller.authentication import (
    verify_controller_token,
)

# Existing Controller service.
from server_runner.sandbox_controller.service import (
    SandboxControllerService,
)

# Database dependency.
from server_runner.runner_service.database import (
    get_db,
)

# Request validation.
from server_runner.sandbox_controller.execution_request import (
    ControllerExecutionRequest,
)

# Sandbox authorization and lifecycle handling.
from server_runner.sandbox_controller.operation_handler import (
    SandboxOperationHandler,
)

# Docker execution pipeline.
from server_runner.sandbox_controller.code_execution_pipeline import (
    CodeExecutionPipeline,
)

# Replay-protection exception.
from server_runner.sandbox_controller.replay import (
    ReplayProtectionError,
)


# ============================================================
# 2. CREATE FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="CodeView Sandbox Controller",

    # Disable automatic API documentation.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


# ============================================================
# 3. INITIALIZE CONTROLLER SERVICE
# ============================================================

controller = SandboxControllerService()


# ============================================================
# 4. HEALTH CHECK
# ============================================================

@app.get("/health")
def health_check():
    """Return the Controller's health status."""

    return controller.health_check()


# ============================================================
# 5. SECURITY SUMMARY
# ============================================================

@app.get("/internal/security")
def security_summary(
    authenticated: bool = Depends(verify_controller_token),
):
    """Return security information to an authenticated service."""

    return controller.get_security_summary()


# ============================================================
# 6. CODE EXECUTION ENDPOINT
# ============================================================

@app.post("/internal/executions")
async def execute_code(
    http_request: Request,

    # Authenticate the internal calling service.
    authenticated: bool = Depends(verify_controller_token),

    # Provide the database session.
    db: Session = Depends(get_db),
):
    """
    Authorize and execute code in a temporary Docker container.

    Steps:
        1. Authenticate the calling service.
        2. Validate the execution request.
        3. Verify sandbox ownership and lifecycle.
        4. Prevent replayed requests.
        5. Execute the source code.
        6. Return the execution result.
    """

    # --------------------------------------------------------
    # STEP 1: VALIDATE THE REQUEST
    # --------------------------------------------------------

    try:
        # Read the incoming JSON body.
        request_body = await http_request.body()

        # Use JSON validation so strict UUID fields accept
        # correctly formatted UUID strings.
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
        # Initialize the operation handler.
        operation_handler = SandboxOperationHandler(db)

        # Verify sandbox ownership, lifecycle, and replay protection.
        operation_handler.handle(
            execution_request.operation_request
        )

    # IMPORTANT:
    # Catch ReplayProtectionError before PermissionError
    # because ReplayProtectionError may inherit from it.
    except ReplayProtectionError as error:

        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except PermissionError as error:

        raise HTTPException(
            status_code=403,
            detail=str(error),
        )  from error

    except ValueError as error:

        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except SQLAlchemyError as error:

        # Temporarily print the original database error.
        traceback.print_exc()

        raise HTTPException(
            status_code=503,
            detail="Sandbox authorization database unavailable.",
        ) from error
    # --------------------------------------------------------
    # STEP 3: EXECUTE THE SOURCE CODE
    # --------------------------------------------------------

    try:
        # Initialize the Docker execution pipeline.
        pipeline = CodeExecutionPipeline()

        # Execute the developer's source code.
        result = pipeline.execute(
            sandbox_id=execution_request.operation_request.sandbox_id,
            language=execution_request.language,
            source=execution_request.source,
        )

        # Return the execution result.
        return result

    except ValueError as error:

        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

    except RuntimeError as error:

        # Temporarily print the original Docker execution error.
        traceback.print_exc()

        raise HTTPException(
            status_code=503,
            detail="Code execution service unavailable.",
        ) from error