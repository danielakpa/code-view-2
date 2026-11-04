"""FastAPI application for the CodeView IDE Gateway."""

from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from server_runner.ide_gateway_service.authentication import (
    verify_gateway_token,
)

from server_runner.ide_gateway_service.runner_client import (
    RunnerAuthorizationError,
    RunnerUnavailableError,
    consume_grant_with_runner,
)


# STEP 1: Create the IDE Gateway application.
app = FastAPI(
    title="CodeView IDE Gateway",
    version="1.0.0",
)


# STEP 2: Define the authorization request structure.
class IDEAuthorizationRequest(BaseModel):
    """
    Data required to authorize access to a developer IDE.
    """

    grant: str = Field(
        min_length=1,
        max_length=4096,
        strict=True,
    )


# STEP 3: Define the authorization response structure.
class IDEAuthorizationResponse(BaseModel):
    """
    Return the verified sandbox identity.

    This does not establish a browser session yet.
    """

    authorized: bool
    sandbox_id: UUID


# STEP 4: Public health-check endpoint.
@app.get("/health")
async def health_check():
    """Check whether the IDE Gateway is running."""

    return {
        "service": "codeview-ide-gateway",
        "status": "healthy",
    }


# STEP 5: Protected internal status endpoint.
@app.get("/internal/status")
async def internal_status(
    _: None = Depends(verify_gateway_token),
):
    """Verify backend-to-Gateway authentication."""

    return {
        "service": "codeview-ide-gateway",
        "authenticated": True,
    }


# STEP 6: Protected IDE authorization endpoint.
@app.post(
    "/internal/ide/authorize",
    response_model=IDEAuthorizationResponse,
)
async def authorize_ide(
    request: IDEAuthorizationRequest,
    _: None = Depends(verify_gateway_token),
):
    """
    Verify a developer's IDE authorization grant.

    The Gateway:
    1. Authenticates the calling backend.
    2. Sends the grant to the Runner.
    3. Receives the verified sandbox UUID.
    4. Returns the authorization result.

    Browser session creation will be implemented separately.
    """

    # STEP 7: Ask the Runner to verify and consume the grant.
    try:
        sandbox_id = await consume_grant_with_runner(
            request.grant
        )

    # STEP 8: Reject invalid or previously used grants.
    except RunnerAuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="IDE authorization rejected.",
        ) from error

    # STEP 9: Handle Runner connection failures.
    except RunnerUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IDE authorization service unavailable.",
        ) from error

    # STEP 10: Return the verified sandbox identity.
    return IDEAuthorizationResponse(
        authorized=True,
        sandbox_id=sandbox_id,
    )