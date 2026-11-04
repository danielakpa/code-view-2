"""FastAPI application for the CodeView IDE Gateway."""

from uuid import UUID
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    status,
)

from server_runner.ide_gateway_service.http_proxy import (
    proxy_ide_http,
)


from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from server_runner.ide_gateway_service.authentication import (
    verify_gateway_token,
)

from server_runner.ide_gateway_service.runner_client import (
    RunnerAuthorizationError,
    RunnerUnavailableError,
    consume_grant_with_runner,
)

from server_runner.ide_gateway_service.sessions import (
    SESSION_LIFETIME_MINUTES,
    ide_session_store,
)

from server_runner.ide_gateway_service.handoff import (
    ide_handoff_store,
)


# STEP 1: Create the FastAPI application.
app = FastAPI(
    title="CodeView IDE Gateway",
    version="1.0.0",
)


# STEP 2: Define the authorization request.
class IDEAuthorizationRequest(BaseModel):
    """Receive a signed IDE authorization grant."""

    grant: str = Field(
        min_length=1,
        max_length=4096,
        strict=True,
    )


# STEP 3: Define the authorization response.
class IDEAuthorizationResponse(BaseModel):
    """Return the authorized sandbox and handoff ticket."""

    authorized: bool
    sandbox_id: UUID
    handoff_ticket: str


# STEP 4: Health-check endpoint.
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


# STEP 6: Verify the grant and create a handoff ticket.
@app.post(
    "/internal/ide/authorize",
    response_model=IDEAuthorizationResponse,
)
async def authorize_ide(
    request: IDEAuthorizationRequest,
    _: None = Depends(verify_gateway_token),
):
    """
    Authorize an IDE and create a one-time handoff ticket.

    This endpoint is called by Django, not the browser.
    """

    # Ask the Runner to verify and consume the grant.
    try:
        sandbox_id = await consume_grant_with_runner(
            request.grant
        )

    except RunnerAuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="IDE authorization rejected.",
        ) from error

    except RunnerUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IDE authorization service unavailable.",
        ) from error

    # Create a short-lived, one-time handoff ticket.
    ticket = ide_handoff_store.create_ticket(
        sandbox_id
    )

    # Prevent caching of the sensitive response.
    response = IDEAuthorizationResponse(
        authorized=True,
        sandbox_id=sandbox_id,
        handoff_ticket=ticket,
    )

    return response


# STEP 7: Exchange a handoff ticket for a browser session.
@app.get("/ide/handoff/{ticket}")
async def complete_ide_handoff(ticket: str):
    """
    Consume a handoff ticket and create a browser IDE session.

    The ticket can be used only once.
    """

    # Consume the ticket.
    sandbox_id = ide_handoff_store.consume_ticket(
        ticket
    )

    # Reject invalid, expired, or reused tickets.
    if sandbox_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired IDE handoff ticket.",
        )

    # Create a browser session.
    session_token = ide_session_store.create_session(
        sandbox_id
    )

    # Redirect the browser to the IDE entry point.
    response = RedirectResponse(
        url="/ide/",
        status_code=status.HTTP_303_SEE_OTHER,
    )

    # Store the session token in a secure cookie.
    response.set_cookie(
        key="codeview_ide_session",
        value=session_token,
        max_age=SESSION_LIFETIME_MINUTES * 60,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/ide",
    )

    # Prevent caching of the redirect response.
    response.headers["Cache-Control"] = "no-store"

    # Avoid leaking the handoff URL to other websites.
    response.headers["Referrer-Policy"] = "no-referrer"

    return response
# STEP 12: Verify the developer's IDE browser session.
def get_authorized_ide_sandbox(
    codeview_ide_session: str | None = Cookie(default=None),
):
    """
    Find the sandbox belonging to an authorized IDE session.

    This dependency will be reused by our HTTP routes.
    """

    # STEP 1: Reject requests without a session cookie.
    if not codeview_ide_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="IDE session is missing.",
        )

    # STEP 2: Look up the browser session.
    session = ide_session_store.get_session(
        codeview_ide_session
    )

    # STEP 3: Reject invalid or expired sessions.
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="IDE session is invalid or expired.",
        )

    # STEP 4: Return the authorized sandbox identity.
    return session.sandbox_id


# STEP 13: Forward requests to the developer's IDE.
@app.api_route(
    "/ide/",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@app.api_route(
    "/ide/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def open_ide(
    request: Request,
    path: str = "",
    sandbox_id: UUID = Depends(get_authorized_ide_sandbox),
):
    """
    Forward authorized browser requests to the correct IDE.

    The browser cannot choose its own sandbox ID.
    """

    # STEP 1: The dependency verifies the browser session.
    # STEP 2: It returns the sandbox linked to that session.
    # STEP 3: Forward the request through the sandbox's Unix socket.
    return await proxy_ide_http(
        request=request,
        sandbox_id=sandbox_id,
        path=path,
    )