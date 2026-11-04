"""Tests for CodeView IDE authorization grants."""

import os
import time

from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase

from server_runner.ide_gateway_service.grants import (
    InvalidIDEGrant,
    create_ide_grant,
    verify_ide_grant,
)


class TestIDEGrants(SimpleTestCase):
    """Verify IDE grant creation and validation."""

    def setUp(self):
        """Prepare test data."""

        # Use a test-only signing secret.
        self.secret = "codeview-test-secret-key-123456789012345"

        # Create a fake sandbox UUID.
        self.sandbox_id = uuid4()

        # Configure the signing secret for each test.
        self.secret_patch = patch.dict(
            os.environ,
            {"CODEVIEW_IDE_GRANT_SECRET": self.secret},
        )

        self.secret_patch.start()
        self.addCleanup(self.secret_patch.stop)

    def test_valid_grant(self):
        """Accept a correctly signed grant."""

        token = create_ide_grant(
            developer_id=12,
            group_id=5,
            sandbox_id=self.sandbox_id,
        )

        payload = verify_ide_grant(token)

        self.assertEqual(payload["developer_id"], 12)
        self.assertEqual(payload["group_id"], 5)
        self.assertEqual(
            payload["sandbox_id"],
            str(self.sandbox_id),
        )

    def test_modified_grant_rejected(self):
        """Reject a grant whose payload has been modified."""

        token = create_ide_grant(12, 5, self.sandbox_id)

        payload, signature = token.split(".")

        # Modify the signed payload without updating its signature.
        modified_payload = (
            ("A" if payload[0] != "A" else "B")
            + payload[1:]
        )

        modified_token = f"{modified_payload}.{signature}"

        with self.assertRaises(InvalidIDEGrant):
            verify_ide_grant(modified_token)

    def test_expired_grant_rejected(self):
        """Reject a grant after its expiration time."""

        # Create the grant at a simulated earlier time.
        with patch(
            "server_runner.ide_gateway_service.grants.time.time",
            return_value=1000,
        ):
            token = create_ide_grant(12, 5, self.sandbox_id)

        # Verify it after the 30-second lifetime.
        with patch(
            "server_runner.ide_gateway_service.grants.time.time",
            return_value=1031,
        ):
            with self.assertRaises(InvalidIDEGrant):
                verify_ide_grant(token)

    def test_invalid_format_rejected(self):
        """Reject malformed grant strings."""

        with self.assertRaises(InvalidIDEGrant):
            verify_ide_grant("not-a-valid-grant")

    def test_invalid_developer_id_rejected(self):
        """Reject invalid developer identifiers."""

        with self.assertRaises(ValueError):
            create_ide_grant(
                developer_id=0,
                group_id=5,
                sandbox_id=self.sandbox_id,
            )

    def test_invalid_sandbox_uuid_rejected(self):
        """Reject invalid sandbox identifiers."""

        with self.assertRaises(ValueError):
            create_ide_grant(
                developer_id=12,
                group_id=5,
                sandbox_id="not-a-uuid",
            )

    def test_missing_secret_rejected(self):
        """Reject grant creation without a signing secret."""

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                create_ide_grant(
                    developer_id=12,
                    group_id=5,
                    sandbox_id=self.sandbox_id,
                )

    def test_grants_have_unique_ids(self):
        """Verify that separate grants receive unique identifiers."""

        first = create_ide_grant(12, 5, self.sandbox_id)
        second = create_ide_grant(12, 5, self.sandbox_id)

        first_payload = verify_ide_grant(first)
        second_payload = verify_ide_grant(second)

        self.assertNotEqual(
            first_payload["grant_id"],
            second_payload["grant_id"],
        )