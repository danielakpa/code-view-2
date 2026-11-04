"""Tests for atomic IDE authorization grant consumption."""

import os

from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase
from sqlalchemy.exc import IntegrityError

from server_runner.ide_gateway_service.grants import create_ide_grant
from server_runner.runner_service.ide_grants import (
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

        # Create a fake developer and sandbox.
        self.developer_id = 12
        self.group_id = 5
        self.sandbox_id = uuid4()

        # Create a valid authorization grant.
        self.token = create_ide_grant(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=self.sandbox_id,
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
        """Accept a valid grant and save its consumption record."""

        result = consume_ide_grant(
            db=self.db,
            token=self.token,
        )

        # The correct sandbox UUID must be returned.
        self.assertEqual(result, self.sandbox_id)

        # The consumed grant must be saved.
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

        with self.assertRaises(IDEGrantRejected):
            consume_ide_grant(
                db=self.db,
                token=self.token,
            )

        # An unauthorized grant must not be saved.
        self.db.add.assert_not_called()
        self.db.commit.assert_not_called()

    def test_sandbox_without_container_rejected(self):
        """Reject access when the sandbox has no Docker container."""

        # Simulate a sandbox that has not been created.
        self.sandbox.container_id = None

        with self.assertRaises(IDEGrantRejected):
            consume_ide_grant(
                db=self.db,
                token=self.token,
            )

        self.db.add.assert_not_called()
        self.db.commit.assert_not_called()