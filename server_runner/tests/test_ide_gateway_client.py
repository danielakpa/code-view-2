"""Test Django communication with the CodeView IDE Gateway."""

from unittest.mock import AsyncMock, patch

import asyncio
import httpx

from django.test import SimpleTestCase, override_settings

from server_runner.ide_gateway_client import (
    IDEGatewayClient,
    IDEGatewayUnavailableError,
)


@override_settings(
    CODEVIEW_IDE_GATEWAY_URL="http://gateway-test:8002",
    CODEVIEW_IDE_GATEWAY_TOKEN="test-gateway-token",
)
class TestIDEGatewayClient(SimpleTestCase):
    """Verify secure Django-to-Gateway communication."""

    @patch("server_runner.ide_gateway_client.httpx.AsyncClient")
    def test_authorize_ide_success(self, mock_client_class):
        """Confirm Django sends the grant and receives a ticket."""

        # Simulate the Gateway's successful response.
        mock_response = AsyncMock()
        mock_response.raise_for_status = lambda: None
        mock_response.json = lambda: {
            "authorized": True,
            "sandbox_id": "123e4567-e89b-12d3-a456-426614174000",
            "handoff_ticket": "test-handoff-ticket",
        }

        # Simulate the HTTP client.
        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response

        mock_client_class.return_value.__aenter__.return_value = (
            mock_http_client
        )

        # Call the real Gateway client.
        result = asyncio.run(
            IDEGatewayClient().authorize_ide("signed-test-grant")
        )

        # Verify the returned ticket.
        self.assertEqual(
            result["handoff_ticket"],
            "test-handoff-ticket",
        )

        # Verify Django contacted the correct endpoint.
        mock_http_client.post.assert_awaited_once_with(
            "http://gateway-test:8002/internal/ide/authorize",
            headers={
                "Authorization": "Bearer test-gateway-token",
            },
            json={
                "grant": "signed-test-grant",
            },
        )

    @patch("server_runner.ide_gateway_client.httpx.AsyncClient")
    def test_gateway_rejects_authorization(self, mock_client_class):
        """Reject unsuccessful Gateway responses."""

        request = httpx.Request(
            "POST",
            "http://gateway-test:8002/internal/ide/authorize",
        )

        response = httpx.Response(
            status_code=403,
            request=request,
        )

        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = response

        mock_client_class.return_value.__aenter__.return_value = (
            mock_http_client
        )

        with self.assertRaises(IDEGatewayUnavailableError):
            asyncio.run(
                IDEGatewayClient().authorize_ide("invalid-grant")
            )

    @patch("server_runner.ide_gateway_client.httpx.AsyncClient")
    def test_gateway_connection_failure(self, mock_client_class):
        """Handle Gateway connection failures safely."""

        mock_http_client = AsyncMock()

        mock_http_client.post.side_effect = httpx.ConnectError(
            "Connection failed."
        )

        mock_client_class.return_value.__aenter__.return_value = (
            mock_http_client
        )

        with self.assertRaises(IDEGatewayUnavailableError):
            asyncio.run(
                IDEGatewayClient().authorize_ide("signed-test-grant")
            )