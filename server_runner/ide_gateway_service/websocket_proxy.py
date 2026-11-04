"""Secure WebSocket forwarding between CodeView and developer IDEs."""

import asyncio

from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect

from websockets.asyncio.client import unix_connect
from websockets.exceptions import ConnectionClosed

from server_runner.ide_gateway_service.socket_connection import (
    get_ide_socket_path,
    validate_ide_socket,
)

from server_runner.ide_gateway_service.sessions import (
    ide_session_store,
)


# Check the developer's session every 5 seconds.
SESSION_CHECK_INTERVAL = 5


async def proxy_ide_websocket(
    websocket: WebSocket,
    sandbox_id: UUID,
    path: str = "",
    session_token: str | None = None,
) -> None:
    """Forward IDE messages while checking session authorization."""

    # 1. Reject unsafe resource paths.
    if (
        path.startswith("/")
        or "\\" in path
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        await websocket.close(code=1008)
        return

    # 2. Require a valid session belonging to this sandbox.
    if not session_token:
        await websocket.close(code=1008)
        return

    session = ide_session_store.get_session(session_token)

    if session is None or session.sandbox_id != sandbox_id:
        await websocket.close(code=1008)
        return

    # 3. Find the Unix socket belonging to this developer's IDE.
    try:
        socket_path = get_ide_socket_path(sandbox_id)
        validate_ide_socket(socket_path)

    except (OSError, ValueError):
        await websocket.close(code=1011)
        return

    # 4. Construct the upstream WebSocket resource path.
    upstream_path = f"/{path}" if path else "/"

    query_string = websocket.scope.get("query_string", b"")

    if query_string:
        upstream_path += "?" + query_string.decode("ascii")

    try:
        # 5. Connect to code-server through its Unix socket.
        async with unix_connect(
            path=str(socket_path),
            uri=f"ws://localhost{upstream_path}",
            open_timeout=10,
            max_size=16 * 1024 * 1024,
        ) as upstream:

            # Accept the authenticated browser connection.
            await websocket.accept()

            async def browser_to_ide():
                """Forward browser messages to the IDE."""

                while True:
                    message = await websocket.receive()

                    if message["type"] == "websocket.disconnect":
                        return

                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

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
                """Disconnect when the developer loses authorization."""

                while True:

                    # Wait before checking again.
                    await asyncio.sleep(SESSION_CHECK_INTERVAL)

                    # Retrieve the current session.
                    current_session = ide_session_store.get_session(
                        session_token
                    )

                    # A revoked or expired session is no longer valid.
                    if (
                        current_session is None
                        or current_session.sandbox_id != sandbox_id
                    ):
                        # Close the browser connection.
                        await websocket.close(code=1008)
                        return

            # 6. Run forwarding and session monitoring concurrently.
            tasks = [
                asyncio.create_task(browser_to_ide()),
                asyncio.create_task(ide_to_browser()),
                asyncio.create_task(monitor_session()),
            ]

            try:
                # Wait until forwarding or session monitoring finishes.
                done, pending = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # Check whether any completed task failed.
                for task in done:
                    if not task.cancelled():
                        error = task.exception()

                        if error is not None:
                            raise error

                # Check whether the browser is still connected.
                browser_task = tasks[0]

                # Check whether the IDE stopped sending messages.
                ide_task = tasks[1]

                # Check whether the session monitor finished.
                monitor_task = tasks[2]

                # If the IDE disconnected first, close the browser.
                if ide_task in done:
                    try:
                        await websocket.close(code=1011)
                    except RuntimeError:
                        pass

                # If the browser disconnected first, cleanup
                # happens in the finally block.
                elif browser_task in done:
                    pass

                # The session monitor already closes the browser
                # with code 1008 when authorization is lost.
                elif monitor_task in done:
                    pass

            finally:
                # Cancel every task that is still running.
                for task in tasks:
                    if not task.done():
                        task.cancel()

                # Wait for all cancelled tasks to finish.
                await asyncio.gather(
                    *tasks,
                    return_exceptions=True,
                )
    except WebSocketDisconnect:
        # The browser disconnected normally.
        pass

    except (OSError, ConnectionClosed, TimeoutError, ValueError):
        # The IDE connection failed or was interrupted.
        try:
            await websocket.close(code=1011)

        except RuntimeError:
            pass