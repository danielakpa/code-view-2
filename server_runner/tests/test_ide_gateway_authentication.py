"""Tests for IDE gateway service authentication."""

import os

from unittest.mock import patch

from django.test import SimpleTestCase
from fastapi import HTTPException

from server_runner.ide_gateway_service.authentication import (
    verify_gateway_token,
)


class TestIDEGatewayAuthentication(SimpleTestCase):
    """Verify that only trusted backend services are accepted."""

    def setUp(self):
        """Prepare a test authentication token."""

        self.token = "test-codeview-gateway-secret"

    @patch.dict(
        os.environ,
        {"CODEVIEW_IDE_GATEWAY_TOKEN": "test-codeview-gateway-secret"},
    )
    def test_valid_token_accepted(self):
        """Accept the correct Bearer token."""

        result = verify_gateway_token(
            authorization=f"Bearer {self.token}"
        )

        self.assertIsNone(result)

    @patch.dict(
        os.environ,
        {"CODEVIEW_IDE_GATEWAY_TOKEN": "test-codeview-gateway-secret"},
    )
    def test_invalid_token_rejected(self):
        """Reject an incorrect authentication token."""

        with self.assertRaises(HTTPException) as context:
            verify_gateway_token(
                authorization="Bearer incorrect-token"
            )

        self.assertEqual(context.exception.status_code, 401)

    @patch.dict(
        os.environ,
        {"CODEVIEW_IDE_GATEWAY_TOKEN": "test-codeview-gateway-secret"},
    )
    def test_missing_token_rejected(self):
        """Reject requests without an authentication header."""

        with self.assertRaises(HTTPException) as context:
            verify_gateway_token(authorization=None)

        self.assertEqual(context.exception.status_code, 401)

    @patch.dict(
        os.environ,
        {"CODEVIEW_IDE_GATEWAY_TOKEN": "test-codeview-gateway-secret"},
    )
    def test_invalid_header_format_rejected(self):
        """Reject authentication headers without Bearer."""

        with self.assertRaises(HTTPException) as context:
            verify_gateway_token(
                authorization=self.token
            )

        self.assertEqual(context.exception.status_code, 401)

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_gateway_configuration_rejected(self):
        """Reject requests when the gateway secret is not configured."""

        with self.assertRaises(HTTPException) as context:
            verify_gateway_token(
                authorization=f"Bearer {self.token}"
            )

        self.assertEqual(context.exception.status_code, 503)