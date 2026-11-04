"""Tests for CodeView IDE HTTP request forwarding."""

import unittest

from unittest.mock import patch
from uuid import uuid4

import httpx

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from server_runner.ide_gateway_service.http_proxy import (
    filter_request_headers,
    filter_response_headers,
    proxy_ide_http,
)


class TestIDEHTTPForwarding(unittest.TestCase):
    """Test how the IDE Gateway forwards HTTP requests."""

    def setUp(self):
        """Prepare a sandbox and test application."""

        # Create a sandbox identity.
        self.sandbox_id = uuid4()

        # Create an isolated FastAPI application.
        self.app = FastAPI()

        # Store requests received by the simulated IDE.
        self.upstream_requests = []

        # Simulate the VS Code container.
        def fake_ide(request: httpx.Request) -> httpx.Response:

            # Record the request for verification.
            self.upstream_requests.append(request)

            return httpx.Response(
                status_code=200,
                content=b"VS Code is running",
                headers={
                    "Content-Type": "text/plain",
                    "Set-Cookie": "upstream-secret=123",
                    "X-IDE-Test": "success",
                },
            )

        # Create a fake HTTP transport.
        self.transport = httpx.MockTransport(fake_ide)

        # Create a fake HTTP client using that transport.
        self.http_client = httpx.AsyncClient(
            transport=self.transport,
            base_url="http://localhost",
        )

        # Keep a reference to the sandbox ID.
        sandbox_id = self.sandbox_id

        # Create a test endpoint that calls our real proxy.
        @self.app.api_route(
            "/test-ide/{path:path}",
            methods=[
                "GET",
                "HEAD",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
                "OPTIONS",
            ],
        )
        async def test_ide(request: Request, path: str):

            return await proxy_ide_http(
                request=request,
                sandbox_id=sandbox_id,
                path=path,
            )

        # Replace the Unix socket client with our fake client.
        self.client_patch = patch(
            "server_runner.ide_gateway_service.http_proxy."
            "create_ide_http_client",
            return_value=self.http_client,
        )

        self.client_patch.start()

        # Create the browser test client.
        self.client = TestClient(self.app)

    def tearDown(self):
        """Clean up test resources."""

        self.client.close()
        self.client_patch.stop()

        # Close the asynchronous HTTP client.
        import asyncio

        asyncio.run(self.http_client.aclose())

    def test_request_reaches_ide(self):
        """A browser request should reach the IDE container."""

        response = self.client.get(
            "/test-ide/",
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.text,
            "VS Code is running",
        )

        self.assertEqual(len(self.upstream_requests), 1)

    def test_request_path_forwarded(self):
        """The proxy must preserve the requested resource path."""

        response = self.client.get(
            "/test-ide/static/app.js?version=1"
        )

        self.assertEqual(response.status_code, 200)

        upstream_request = self.upstream_requests[0]

        self.assertEqual(
            upstream_request.url.path,
            "/static/app.js",
        )

        self.assertEqual(
            upstream_request.url.query,
            b"version=1",
        )

    def test_sensitive_request_headers_removed(self):
        """Browser credentials must not reach the IDE container."""

        headers = {
            "Host": "codeview.example",
            "Cookie": "codeview_ide_session=secret",
            "Authorization": "Bearer secret",
            "X-Forwarded-For": "192.0.2.10",
            "X-Custom-Header": "allowed",
        }

        filtered = filter_request_headers(headers)

        self.assertNotIn("Host", filtered)
        self.assertNotIn("Cookie", filtered)
        self.assertNotIn("Authorization", filtered)
        self.assertNotIn("X-Forwarded-For", filtered)

        self.assertEqual(
            filtered["X-Custom-Header"],
            "allowed",
        )

    def test_sensitive_headers_not_forwarded(self):
        """The real proxy must remove sensitive browser headers."""

        response = self.client.get(
            "/test-ide/",
            headers={
                "Authorization": "Bearer secret",
                "X-Forwarded-For": "192.0.2.10",
                "X-Custom-Header": "allowed",
            },
            cookies={
                "codeview_ide_session": "secret",
            },
        )

        self.assertEqual(response.status_code, 200)

        upstream_headers = self.upstream_requests[0].headers

        self.assertNotIn("cookie", upstream_headers)
        self.assertNotIn("authorization", upstream_headers)
        self.assertNotIn("x-forwarded-for", upstream_headers)

        self.assertEqual(
            upstream_headers["x-custom-header"],
            "allowed",
        )

    def test_response_headers_filtered(self):
        """Upstream cookies must not reach the browser."""

        response = self.client.get(
            "/test-ide/",
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.headers["x-ide-test"],
            "success",
        )

        self.assertNotIn(
            "set-cookie",
            response.headers,
        )

    def test_response_filter_function(self):
        """The response filter must remove unsafe headers."""

        headers = {
            "Set-Cookie": "secret=123",
            "Connection": "keep-alive",
            "Content-Encoding": "gzip",
            "X-IDE-Test": "allowed",
        }

        filtered = filter_response_headers(headers)

        self.assertNotIn("Set-Cookie", filtered)
        self.assertNotIn("Connection", filtered)
        self.assertNotIn("Content-Encoding", filtered)

        self.assertEqual(
            filtered["X-IDE-Test"],
            "allowed",
        )

    def test_invalid_path_rejected(self):
        """Path traversal attempts must be rejected."""

        response = self.client.get(
            "/test-ide/../secret",
            follow_redirects=False,
        )

        self.assertIn(
            response.status_code,
            (400, 404),
        )

    def test_unavailable_ide_returns_502(self):
        """Connection failures must return HTTP 502."""

        # Simulate a connection failure.
        with patch(
            "server_runner.ide_gateway_service.http_proxy."
            "create_ide_http_client",
            side_effect=OSError("Socket unavailable"),
        ):

            response = self.client.get(
                "/test-ide/",
            )

        self.assertEqual(response.status_code, 502)

        self.assertEqual(
            response.json()["detail"],
            "IDE container is unavailable.",
        )


if __name__ == "__main__":
    unittest.main()