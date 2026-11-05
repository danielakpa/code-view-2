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

from fastapi import WebSocket

from server_runner.ide_gateway_service.http_proxy import (
    proxy_ide_http,
)

import os
from urllib.parse import urlsplit

from server_runner.ide_gateway_service.websocket_proxy import (
    proxy_ide_websocket,
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

    # STEP 1: Ask the Runner to verify and consume the grant.
    try:
        identity = await consume_grant_with_runner(
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

    # STEP 2: Create a one-time ticket containing the verified identity.
    ticket = ide_handoff_store.create_ticket(
        identity
    )

    # STEP 3: Return the authorized sandbox and handoff ticket.
    response = IDEAuthorizationResponse(
        authorized=True,
        sandbox_id=identity.sandbox_id,
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

    # STEP 1: Consume the one-time handoff ticket.
    handoff = ide_handoff_store.consume_ticket(
        ticket
    )

    # STEP 2: Reject invalid, expired, or reused tickets.
    if handoff is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired IDE handoff ticket.",
        )
    # STEP 3: Create a browser session using the verified identities.
    session_token = ide_session_store.create_session(
        developer_id=handoff.developer_id,
        group_id=handoff.group_id,
        sandbox_id=handoff.sandbox_id,
    )

    # STEP 4: Redirect the browser to the IDE entry point.
    response = RedirectResponse(
        url="/ide/",
        status_code=status.HTTP_303_SEE_OTHER,
    )

    # STEP 5: Store the session token in a secure cookie.
    response.set_cookie(
        key="codeview_ide_session",
        value=session_token,
        max_age=SESSION_LIFETIME_MINUTES * 60,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/ide",
    )

    # STEP 6: Prevent caching of the redirect response.
    response.headers["Cache-Control"] = "no-store"

    # STEP 7: Avoid leaking the handoff URL to other websites.
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
    Forward authenticated HTTP requests to the developer's IDE.

    Protect state-changing requests against cross-origin attacks.
    """

    # STEP 1: Identify the HTTP request method.
    method = request.method.upper()

    # STEP 2: Identify methods that can change IDE data.
    unsafe_methods = {
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }

    # STEP 3: Read the browser's Origin header.
    origin = request.headers.get("origin")

    # STEP 4: Validate Origin whenever the browser supplies it.
    if origin is not None:
        if not is_allowed_websocket_origin(origin):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Untrusted IDE request origin.",
            )

    # STEP 5: Require Origin for state-changing requests.
    if method in unsafe_methods and origin is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="IDE request origin is required.",
        )

    # STEP 6: Forward the authorized request.
    return await proxy_ide_http(
        request=request,
        sandbox_id=sandbox_id,
        path=path,
    )

# STEP 14: Validate the browser's WebSocket Origin.
def is_allowed_websocket_origin(origin: str | None) -> bool:
    """
    Accept only the explicitly configured IDE browser origin.

    Example:
        https://ide.example.com

    Do not use the incoming Host header to decide which
    origins are trusted.
    """

    # Read the trusted public IDE origin.
    allowed_origin = os.environ.get(
        "CODEVIEW_IDE_PUBLIC_ORIGIN",
        "",
    ).strip()

    # Reject requests if the origin is not configured.
    if not allowed_origin or not origin:
        return False

    try:
        # Parse the configured origin.
        expected = urlsplit(allowed_origin)

        # Parse the browser's Origin header.
        supplied = urlsplit(origin)

        # Only accept HTTP or HTTPS origins.
        if expected.scheme not in {"http", "https"}:
            return False

        # Origins must not contain paths, queries, or fragments.
        for parsed in (expected, supplied):
            if (
                not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                return False

        # Compare scheme, hostname, and port.
        return (
            supplied.scheme == expected.scheme
            and supplied.hostname == expected.hostname
            and supplied.port == expected.port
        )

    except ValueError:
        # Reject malformed origins.
        return False


# STEP 15: Authenticate the WebSocket browser session.
def get_websocket_sandbox(websocket: WebSocket) -> UUID | None:
    """
    Return the sandbox belonging to the browser session.

    The browser cannot choose its own sandbox ID.
    """

    # Read the session cookie from the WebSocket request.
    session_token = websocket.cookies.get(
        "codeview_ide_session"
    )

    # Reject missing session tokens.
    if not session_token:
        return None

    # Look up the session in our Gateway session store.
    session = ide_session_store.get_session(
        session_token
    )

    # Reject invalid, revoked, or expired sessions.
    if session is None:
        return None

    # Return the authorized sandbox.
    return session.sandbox_id

# STEP 16: Handle authorized IDE WebSocket connections.
@app.websocket("/ide/")
@app.websocket("/ide/{path:path}")
async def open_ide_websocket(
    websocket: WebSocket,
    path: str = "",
):
    """Connect a browser to its IDE while monitoring session validity."""

    # Step 1: Read the browser's Origin header.
    origin = websocket.headers.get("origin")

    # Step 2: Reject requests from untrusted websites.
    if not is_allowed_websocket_origin(origin):
        await websocket.close(code=1008)
        return

    # Step 3: Read the browser's IDE session cookie.
    session_token = websocket.cookies.get(
        "codeview_ide_session"
    )

    # Step 4: Reject browsers without a session.
    if not session_token:
        await websocket.close(code=1008)
        return

    # Step 5: Check whether the session exists and is valid.
    session = ide_session_store.get_session(session_token)

    if session is None:
        await websocket.close(code=1008)
        return

    # Step 6: Identify the developer's authorized sandbox.
    sandbox_id = session.sandbox_id

    # Step 7: Start forwarding WebSocket messages to the IDE.
    # Session monitoring will be added inside the proxy.
    await proxy_ide_websocket(
        websocket=websocket,
        sandbox_id=sandbox_id,
        path=path,
        session_token=session_token,
    )