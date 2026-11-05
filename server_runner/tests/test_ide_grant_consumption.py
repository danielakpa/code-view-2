"""Tests for atomic IDE authorization grant consumption."""

import os

from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase
from sqlalchemy.exc import IntegrityError

from server_runner.ide_gateway_service.grants import create_ide_grant

from server_runner.runner_service.ide_grants import (
    AuthorizedIDEIdentity,
    IDEGrantRejected,
    consume_ide_grant,
)


class TestIDEGrantConsumption(SimpleTestCase):
    """Test IDE grant validation and database operations."""

    def setUp(self):
        """Prepare a developer, sandbox, and signing secret."""

        # Use a test-only signing secret.
        self.secret_patch = patch.dict(
            os.environ,
            {
                "CODEVIEW_IDE_GRANT_SECRET":
                    "codeview-test-secret-key-123456789012345"
            },
        )

        self.secret_patch.start()
        self.addCleanup(self.secret_patch.stop)
        # Identify the developer and private development group.
        self.developer_id = 12
        self.group_id = 5

        # Identify the developer's private Docker sandbox.
        self.sandbox_id = uuid4()

        # Identify the developer's current group membership.
        self.membership_generation = uuid4()

        # Create a signed grant containing the membership generation.
        self.token = create_ide_grant(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=self.sandbox_id,
            membership_generation=self.membership_generation,
        )

        # Create a fake database session.
        self.db = Mock()

        # Simulate a sandbox with a Docker container.
        self.sandbox = Mock()
        self.sandbox.container_id = "a" * 64

        # Return the sandbox when the database is queried.
        self.db.query.return_value.filter.return_value.first.return_value = (
            self.sandbox
        )

    def test_valid_grant_consumed(self):
        """Accept a valid grant and return the verified IDE identity."""

        result = consume_ide_grant(
            db=self.db,
            token=self.token,
        )

        # STEP 1: Verify the returned object type.
        self.assertIsInstance(
            result,
            AuthorizedIDEIdentity,
        )

        # STEP 2: Verify the developer identity.
        self.assertEqual(
            result.developer_id,
            self.developer_id,
        )

        # STEP 3: Verify the private group identity.
        self.assertEqual(
            result.group_id,
            self.group_id,
        )

        # STEP 4: Verify the sandbox identity.
        self.assertEqual(
            result.sandbox_id,
            self.sandbox_id,
        )

        # STEP 5: Verify that the consumed grant was saved.
        self.db.add.assert_called_once()
        self.db.commit.assert_called_once()

    def test_reused_grant_rejected(self):
        """Reject a grant when PostgreSQL reports a duplicate."""

        # Simulate a database uniqueness constraint failure.
        self.db.commit.side_effect = IntegrityError(
            statement="INSERT INTO ide_grant_consumptions",
            params={},
            orig=Exception("duplicate key"),
        )

        # A reused grant must raise an authorization error.
        with self.assertRaises(IDEGrantRejected):
            consume_ide_grant(
                db=self.db,
                token=self.token,
            )

        # Failed transactions must be rolled back.
        self.db.rollback.assert_called_once()

    def test_wrong_sandbox_owner_rejected(self):
        """Reject a grant when no matching sandbox is found."""

        # The database returns no matching sandbox.
        self.db.query.return_value.filter.return_value.first.return_value = (
            None
        )

        # The grant must be rejected.
        with self.assertRaises(IDEGrantRejected):
            consume_ide_grant(
                db=self.db,
                token=self.token,
            )

        # Unauthorized grants must never be saved.
        self.db.add.assert_not_called()
        self.db.commit.assert_not_called()

    def test_sandbox_without_container_rejected(self):
        """Reject access when the sandbox has no Docker container."""

        # Simulate a sandbox that has not been created.
        self.sandbox.container_id = None

        # Reject access to the missing Docker IDE.
        with self.assertRaises(IDEGrantRejected):
            consume_ide_grant(
                db=self.db,
                token=self.token,
            )

        # Rejected grants must not be saved.
        self.db.add.assert_not_called()
        self.db.commit.assert_not_called()