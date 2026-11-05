"""Tests for CodeView IDE authorization grants."""

import os

from unittest.mock import patch
from uuid import UUID, uuid4

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

        # Represent the developer's current group membership.
        self.membership_generation = uuid4()

        # Configure the signing secret for each test.
        self.secret_patch = patch.dict(
            os.environ,
            {"CODEVIEW_IDE_GRANT_SECRET": self.secret},
        )

        self.secret_patch.start()
        self.addCleanup(self.secret_patch.stop)

    def create_test_grant(self):
        """
        Create a valid test grant.

        This helper prevents us from repeating the same
        arguments in every test.
        """

        return create_ide_grant(
            developer_id=12,
            group_id=5,
            sandbox_id=self.sandbox_id,
            membership_generation=self.membership_generation,
        )

    def test_valid_grant(self):
        """Accept a correctly signed grant."""

        token = self.create_test_grant()

        payload = verify_ide_grant(token)

        self.assertEqual(payload["developer_id"], 12)
        self.assertEqual(payload["group_id"], 5)

        self.assertEqual(
            payload["sandbox_id"],
            str(self.sandbox_id),
        )

        # Verify that membership generation is included.
        self.assertEqual(
            payload["membership_generation"],
            str(self.membership_generation),
        )

    def test_modified_grant_rejected(self):
        """Reject a grant whose signed payload was modified."""

        token = self.create_test_grant()

        payload, signature = token.split(".")

        # Modify the payload without updating its signature.
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
            token = self.create_test_grant()

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
                membership_generation=self.membership_generation,
            )

    def test_invalid_sandbox_uuid_rejected(self):
        """Reject invalid sandbox identifiers."""

        with self.assertRaises(ValueError):
            create_ide_grant(
                developer_id=12,
                group_id=5,
                sandbox_id="not-a-uuid",
                membership_generation=self.membership_generation,
            )

    def test_missing_secret_rejected(self):
        """Reject grant creation without a signing secret."""

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                self.create_test_grant()

    def test_grants_have_unique_ids(self):
        """Verify that separate grants receive unique identifiers."""

        first = self.create_test_grant()
        second = self.create_test_grant()

        first_payload = verify_ide_grant(first)
        second_payload = verify_ide_grant(second)

        self.assertNotEqual(
            first_payload["grant_id"],
            second_payload["grant_id"],
        )

    def test_invalid_membership_generation_rejected(self):
        """Reject grants containing invalid membership identifiers."""

        invalid_values = [
            None,
            "",
            "not-a-uuid",
            UUID(int=0),
            123,
        ]

        for invalid_value in invalid_values:
            with self.subTest(value=invalid_value):

                with self.assertRaises(ValueError):
                    create_ide_grant(
                        developer_id=12,
                        group_id=5,
                        sandbox_id=self.sandbox_id,
                        membership_generation=invalid_value,
                    )

    def test_membership_generations_are_preserved(self):
        """
        Verify that different memberships produce different claims.

        A developer who leaves and rejoins must receive a
        different membership generation.
        """

        old_generation = uuid4()
        new_generation = uuid4()

        old_token = create_ide_grant(
            developer_id=12,
            group_id=5,
            sandbox_id=self.sandbox_id,
            membership_generation=old_generation,
        )

        new_token = create_ide_grant(
            developer_id=12,
            group_id=5,
            sandbox_id=self.sandbox_id,
            membership_generation=new_generation,
        )

        old_payload = verify_ide_grant(old_token)
        new_payload = verify_ide_grant(new_token)

        self.assertEqual(
            old_payload["membership_generation"],
            str(old_generation),
        )

        self.assertEqual(
            new_payload["membership_generation"],
            str(new_generation),
        )

        self.assertNotEqual(
            old_payload["membership_generation"],
            new_payload["membership_generation"],
        )