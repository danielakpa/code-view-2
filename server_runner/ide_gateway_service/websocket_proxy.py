"""Secure WebSocket forwarding between CodeView and developer IDEs."""

import asyncio
import logging

from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from websockets.asyncio.client import unix_connect
from websockets.exceptions import ConnectionClosed

from server_runner.ide_gateway_service.membership_client import (
    MembershipAuthorizationError,
    MembershipServiceUnavailableError,
    validate_ide_membership,
)

from server_runner.ide_gateway_service.socket_connection import (
    get_ide_socket_path,
    validate_ide_socket,
)

from server_runner.ide_gateway_service.sessions import (
    ide_session_store,
)


# Logger for WebSocket Gateway errors.
logger = logging.getLogger(__name__)

# Recheck the developer's authorization every 5 seconds.
SESSION_CHECK_INTERVAL = 5


async def close_websocket_safely(
    websocket: WebSocket,
    code: int = 1000,
) -> None:
    """Close a WebSocket without raising expected disconnect errors."""

    # Avoid sending a second close frame.
    if websocket.application_state == WebSocketState.DISCONNECTED:
        return

    try:
        await websocket.close(code=code)

    except (RuntimeError, OSError):
        # The browser may already have disconnected.
        pass


async def proxy_ide_websocket(
    websocket: WebSocket,
    sandbox_id: UUID,
    path: str = "",
    session_token: str | None = None,
) -> None:
    """Forward IDE messages while continuously validating authorization."""

    # ---------------------------------------------------------
    # STEP 1: Reject unsafe resource paths.
    # ---------------------------------------------------------

    if (
        path.startswith("/")
        or "\\" in path
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        await close_websocket_safely(websocket, code=1008)
        return

    # ---------------------------------------------------------
    # STEP 2: Require an authenticated IDE session.
    # ---------------------------------------------------------

    if not session_token:
        await close_websocket_safely(websocket, code=1008)
        return

    session = ide_session_store.get_session(session_token)

    # Reject missing sessions or sessions for another sandbox.
    if session is None or session.sandbox_id != sandbox_id:
        await close_websocket_safely(websocket, code=1008)
        return

    # ---------------------------------------------------------
    # STEP 3: Validate membership with Django.
    # ---------------------------------------------------------

    async def check_membership() -> bool:
        """Check local session identity and current Django membership."""

        # Read the current session.
        current_session = ide_session_store.get_session(
            session_token
        )

        # Reject expired or revoked sessions.
        if current_session is None:
            return False

        # Reject sessions belonging to another sandbox.
        if current_session.sandbox_id != sandbox_id:
            return False

        # Reject sessions whose identity has changed.
        if (
            current_session.developer_id != session.developer_id
            or current_session.group_id != session.group_id
            or current_session.membership_generation
            != session.membership_generation
        ):
            return False

        # Ask Django whether this developer still has access.
        try:
            authorized = await validate_ide_membership(
                developer_id=session.developer_id,
                group_id=session.group_id,
                membership_generation=session.membership_generation,
            )

        except MembershipAuthorizationError:
            # Django confirmed that access is no longer valid.
            ide_session_store.revoke_session(session_token)

            return False

        except MembershipServiceUnavailableError:
            # Django could not confirm authorization.
            # Fail closed instead of trusting old membership data.
            return False

        if not authorized:
            # Reject explicit authorization denial.
            ide_session_store.revoke_session(session_token)

            return False

        # Recheck the session after the asynchronous Django request.
        # It might have been revoked while waiting for the response.
        latest_session = ide_session_store.get_session(
            session_token
        )

        if latest_session is None:
            return False

        if latest_session.sandbox_id != sandbox_id:
            return False

        if (
            latest_session.developer_id != session.developer_id
            or latest_session.group_id != session.group_id
            or latest_session.membership_generation
            != session.membership_generation
        ):
            return False

        return True

    # Validate membership before connecting to the IDE.
    if not await check_membership():
        await close_websocket_safely(websocket, code=1008)
        return

    # ---------------------------------------------------------
    # STEP 4: Locate and validate the developer's Unix socket.
    # ---------------------------------------------------------

    try:
        socket_path = get_ide_socket_path(sandbox_id)

        validate_ide_socket(socket_path)

    except (OSError, ValueError):
        logger.exception(
            "Unable to access IDE socket for sandbox %s",
            sandbox_id,
        )

        await close_websocket_safely(websocket, code=1011)
        return

    # ---------------------------------------------------------
    # STEP 5: Construct the upstream WebSocket resource path.
    # ---------------------------------------------------------

    upstream_path = f"/{path}" if path else "/"

    query_string = websocket.scope.get("query_string", b"")

    if query_string:
        try:
            upstream_path += "?" + query_string.decode("ascii")

        except UnicodeDecodeError:
            await close_websocket_safely(websocket, code=1008)
            return

    # ---------------------------------------------------------
    # STEP 6: Connect to the developer's IDE.
    # ---------------------------------------------------------

    try:
        async with unix_connect(
            path=str(socket_path),
            uri=f"ws://localhost{upstream_path}",
            open_timeout=10,
            max_size=16 * 1024 * 1024,
        ) as upstream:

            # Membership might change while the socket is opening.
            if not await check_membership():
                await close_websocket_safely(
                    websocket,
                    code=1008,
                )
                return

            # Accept the authenticated browser connection.
            await websocket.accept()

            async def browser_to_ide():
                """Forward browser messages to the IDE."""

                while True:
                    try:
                        message = await websocket.receive()

                    except WebSocketDisconnect:
                        return

                    except asyncio.CancelledError:
                        raise

                    # Stop when the browser disconnects.
                    if message["type"] == "websocket.disconnect":
                        return

                    # Forward binary messages.
                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

                    # Forward text messages.
                    elif message.get("text") is not None:
                        await upstream.send(message["text"])

            async def ide_to_browser():
                """Forward IDE messages to the browser."""

                async for message in upstream:

                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)

                    else:
                        await websocket.send_text(message)

            async def monitor_session():
                """Recheck Django membership while the IDE is connected."""

                while True:
                    # Wait before checking again.
                    await asyncio.sleep(SESSION_CHECK_INTERVAL)

                    # Check both the local session and Django membership.
                    authorized = await check_membership()

                    # End the connection if authorization fails.
                    if not authorized:
                        return

            # -------------------------------------------------
            # STEP 7: Start forwarding and monitoring tasks.
            # -------------------------------------------------

            tasks = [
                asyncio.create_task(browser_to_ide()),
                asyncio.create_task(ide_to_browser()),
                asyncio.create_task(monitor_session()),
            ]

            try:
                # Wait until a forwarding or monitoring task finishes.
                done, pending = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                browser_task = tasks[0]
                ide_task = tasks[1]
                monitor_task = tasks[2]

                # Propagate unexpected task errors.
                for task in done:
                    if not task.cancelled():
                        error = task.exception()

                        if error is not None:
                            raise error

                # Check the current local session.
                current_session = ide_session_store.get_session(
                    session_token
                )

                session_invalid = (
                    current_session is None
                    or current_session.sandbox_id != sandbox_id
                )

                if session_invalid or monitor_task in done:
                    # Membership was denied, validation failed,
                    # or the local session expired.
                    await close_websocket_safely(
                        websocket,
                        code=1008,
                    )

                elif ide_task in done:
                    # The IDE connection ended unexpectedly.
                    await close_websocket_safely(
                        websocket,
                        code=1011,
                    )

                elif browser_task in done:
                    # Normal browser disconnect.
                    pass

            finally:
                # Cancel unfinished tasks.
                for task in tasks:
                    if not task.done():
                        task.cancel()

                # Wait for all tasks to finish.
                await asyncio.gather(
                    *tasks,
                    return_exceptions=True,
                )

    except WebSocketDisconnect:
        # The browser disconnected normally.
        return

    except asyncio.CancelledError:
        # Preserve cancellation during server shutdown.
        raise

    except (OSError, ConnectionClosed, TimeoutError, ValueError):
        # Handle known IDE connection failures.
        logger.exception(
            "IDE WebSocket connection failed for sandbox %s",
            sandbox_id,
        )

        await close_websocket_safely(
            websocket,
            code=1011,
        )

    except Exception:
        # Log unexpected server-side errors.
        logger.exception(
            "Unexpected IDE WebSocket error for sandbox %s",
            sandbox_id,
        )

        await close_websocket_safely(
            websocket,
            code=1011,
        )