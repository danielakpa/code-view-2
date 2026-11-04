"""Forward authorized HTTP requests to CodeView IDE containers."""

import httpx

from uuid import UUID

from fastapi import HTTPException, Request, status
from fastapi.responses import Response

from server_runner.ide_gateway_service.socket_connection import (
    create_ide_http_client,
)


# Headers that must not be forwarded between connections.
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

# Headers that the Gateway must manage itself.
REQUEST_HEADERS_TO_REMOVE = HOP_BY_HOP_HEADERS | {
    "host",
    "cookie",
    "authorization",
    "proxy-connection",
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
}

# Do not forward upstream cookies or unsafe routing headers.
RESPONSE_HEADERS_TO_REMOVE = HOP_BY_HOP_HEADERS | {
    "set-cookie",
    "content-length",
    "content-encoding",
}


def filter_request_headers(headers) -> dict[str, str]:
    """Remove headers that should not reach the IDE container."""

    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in REQUEST_HEADERS_TO_REMOVE
    }


def filter_response_headers(headers) -> dict[str, str]:
    """Remove headers that should not be returned to the browser."""

    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in RESPONSE_HEADERS_TO_REMOVE
    }


async def proxy_ide_http(
    request: Request,
    sandbox_id: UUID,
    path: str = "",
) -> Response:
    """
    Forward an HTTP request to the authorized IDE container.

    The sandbox ID must come from a verified browser session.
    """

    # STEP 1: Only allow supported HTTP methods.
    allowed_methods = {
        "GET",
        "HEAD",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    }

    if request.method not in allowed_methods:
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="HTTP method not supported.",
        )

    # STEP 2: Reject paths that could escape the IDE URL prefix.
    if (
        path.startswith("/")
        or "\\" in path
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid IDE path.",
        )

    # STEP 3: Read the browser request body.
    body = await request.body()

    # STEP 4: Remove sensitive browser headers.
    headers = filter_request_headers(request.headers)

    # STEP 5: Build the upstream URL.
    upstream_path = f"/{path}" if path else "/"

    # Preserve the query string.
    query_string = request.url.query

    if query_string:
        upstream_path += f"?{query_string}"

    try:
        # STEP 6: Connect through the authorized sandbox's Unix socket.
        async with create_ide_http_client(sandbox_id) as client:

            # STEP 7: Forward the HTTP request.
            upstream_response = await client.request(
                method=request.method,
                url=upstream_path,
                headers=headers,
                content=body,
            )

    except (OSError, ValueError, httpx.HTTPError) as error:
        # Do not expose filesystem paths or internal errors.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="IDE container is unavailable.",
        ) from error

    # STEP 8: Remove unsafe upstream response headers.
    response_headers = filter_response_headers(
        upstream_response.headers
    )

    # STEP 9: Return the IDE response to the browser.
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
    )