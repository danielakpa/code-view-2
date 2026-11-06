"""Test authorization and sandbox validation for the IDE gateway."""

import asyncio

from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase

from server_runner.ide_gateway import (
    authorize_ide_request,
    get_authorized_ide,
)


class TestIDEGateway(SimpleTestCase):
    """Verify that developers can access only authorized IDEs."""

    def setUp(self):
        """Prepare fake developers and a private group."""

        # STEP 1: Create a fake authenticated developer.
        self.developer = Mock()
        self.developer.pk = 12
        self.developer.is_authenticated = True

        # STEP 2: Create a fake private development group.
        self.group = Mock()
        self.group.pk = 5
        self.group.is_private = True

        # STEP 3: Create a valid sandbox UUID.
        self.sandbox_id = uuid4()

        # STEP 4: Create a valid membership generation.
        self.membership_generation = uuid4()

    @patch(
        "server_runner.ide_gateway."
        "get_developer_ide_membership_generation"
    )
    @patch("server_runner.ide_gateway.ProjectGroup.objects")
    def test_authorized_developer(
        self,
        mock_groups,
        mock_generation,
    ):
        """Allow an authorized developer to access their IDE."""

        # STEP 1: Simulate finding the private group.
        mock_groups.filter.return_value.first.return_value = self.group

        # STEP 2: Return the developer's current generation.
        mock_generation.return_value = self.membership_generation

        # STEP 3: Authorize the developer.
        result = asyncio.run(
            authorize_ide_request(
                user=self.developer,
                group_id=5,
            )
        )

        # STEP 4: Verify all three returned values.
        self.assertEqual(
            result,
            (
                12,
                5,
                self.membership_generation,
            ),
        )

        # STEP 5: Confirm the trusted authorization was called.
        mock_generation.assert_called_once_with(
            user=self.developer,
            group=self.group,
        )

    @patch("server_runner.ide_gateway.ProjectGroup.objects")
    def test_anonymous_developer_rejected(self, mock_groups):
        """Reject developers who have not logged in."""

        self.developer.is_authenticated = False

        with self.assertRaises(PermissionError):
            asyncio.run(
                authorize_ide_request(
                    user=self.developer,
                    group_id=5,
                )
            )

        # Authentication happens before database access.
        mock_groups.filter.assert_not_called()

    @patch(
        "server_runner.ide_gateway."
        "get_developer_ide_membership_generation"
    )
    @patch("server_runner.ide_gateway.ProjectGroup.objects")
    def test_unauthorized_group_member_rejected(
        self,
        mock_groups,
        mock_generation,
    ):
        """Reject developers without group permission."""

        mock_groups.filter.return_value.first.return_value = self.group

        # Simulate Django denying membership authorization.
        mock_generation.side_effect = PermissionError(
            "Developer is not an approved member."
        )

        with self.assertRaises(PermissionError):
            asyncio.run(
                authorize_ide_request(
                    user=self.developer,
                    group_id=5,
                )
            )

        mock_generation.assert_called_once_with(
            user=self.developer,
            group=self.group,
        )

    @patch(
        "server_runner.ide_gateway."
        "get_developer_ide_membership_generation"
    )
    @patch("server_runner.ide_gateway.ProjectGroup.objects")
    def test_nonexistent_group_rejected(
        self,
        mock_groups,
        mock_generation,
    ):
        """Reject requests for nonexistent private groups."""

        mock_groups.filter.return_value.first.return_value = None

        with self.assertRaises(PermissionError):
            asyncio.run(
                authorize_ide_request(
                    user=self.developer,
                    group_id=999,
                )
            )

        # Membership authorization must not run.
        mock_generation.assert_not_called()

    @patch(
        "server_runner.ide_gateway."
        "get_developer_ide_membership_generation"
    )
    @patch("server_runner.ide_gateway.ProjectGroup.objects")
    def test_invalid_membership_generation_rejected(
        self,
        mock_groups,
        mock_generation,
    ):
        """Reject an invalid membership generation."""

        mock_groups.filter.return_value.first.return_value = self.group

        # An invalid generation must never authorize IDE access.
        mock_generation.return_value = None

        with self.assertRaises(PermissionError):
            asyncio.run(
                authorize_ide_request(
                    user=self.developer,
                    group_id=5,
                )
            )

    @patch(
        "server_runner.ide_gateway.authorize_ide_request"
    )
    @patch(
        "server_runner.ide_gateway.CodeExecutionRunnerClient"
    )
    def test_valid_sandbox_uuid(
        self,
        mock_runner_class,
        mock_authorize,
    ):
        """Accept a valid sandbox UUID from the trusted Runner."""

        # STEP 1: Return all three authorization values.
        mock_authorize.return_value = (
            12,
            5,
            self.membership_generation,
        )

        # STEP 2: Simulate a running IDE.
        mock_ensure_ide = AsyncMock(
            return_value={
                "sandbox_id": str(self.sandbox_id),
                "status": "running",
                "ready": True,
            }
        )

        mock_runner_class.return_value.ensure_ide = mock_ensure_ide

        # STEP 3: Obtain the authorized IDE.
        result = asyncio.run(
            get_authorized_ide(
                user=self.developer,
                group_id=5,
            )
        )

        # STEP 4: Verify the sandbox UUID.
        self.assertEqual(
            result,
            self.sandbox_id,
        )

        # STEP 5: Verify the Runner received the correct IDs.
        mock_ensure_ide.assert_awaited_once_with(
            developer_id=12,
            group_id=5,
        )

        # STEP 6: Verify membership was checked twice.
        self.assertEqual(
            mock_authorize.await_count,
            2,
        )

    @patch(
        "server_runner.ide_gateway.authorize_ide_request"
    )
    @patch(
        "server_runner.ide_gateway.CodeExecutionRunnerClient"
    )
    def test_invalid_sandbox_uuid_rejected(
        self,
        mock_runner_class,
        mock_authorize,
    ):
        """Reject invalid sandbox identities from the Runner."""

        mock_authorize.return_value = (
            12,
            5,
            self.membership_generation,
        )

        mock_runner_class.return_value.ensure_ide = AsyncMock(
            return_value={
                "sandbox_id": "invalid-uuid",
                "status": "running",
                "ready": True,
            }
        )

        with self.assertRaises(RuntimeError):
            asyncio.run(
                get_authorized_ide(
                    user=self.developer,
                    group_id=5,
                )
            )

        # Invalid sandbox identity prevents the second check.
        self.assertEqual(
            mock_authorize.await_count,
            1,
        )

    @patch(
        "server_runner.ide_gateway.authorize_ide_request"
    )
    @patch(
        "server_runner.ide_gateway.CodeExecutionRunnerClient"
    )
    def test_ide_not_running_rejected(
        self,
        mock_runner_class,
        mock_authorize,
    ):
        """Reject an IDE that is not running."""

        mock_authorize.return_value = (
            12,
            5,
            self.membership_generation,
        )

        mock_runner_class.return_value.ensure_ide = AsyncMock(
            return_value={
                "sandbox_id": str(self.sandbox_id),
                "status": "stopped",
                "ready": False,
            }
        )

        with self.assertRaises(RuntimeError):
            asyncio.run(
                get_authorized_ide(
                    user=self.developer,
                    group_id=5,
                )
            )

    @patch(
        "server_runner.ide_gateway.authorize_ide_request"
    )
    @patch(
        "server_runner.ide_gateway.CodeExecutionRunnerClient"
    )
    def test_membership_changed_during_startup_rejected(
        self,
        mock_runner_class,
        mock_authorize,
    ):
        """
        Reject access if membership changes while the IDE starts.
        """

        # STEP 1: Create a different membership generation.
        new_generation = uuid4()

        # STEP 2: First authorization succeeds with the old generation.
        # Second authorization returns a different generation.
        mock_authorize.side_effect = [
            (12, 5, self.membership_generation),
            (12, 5, new_generation),
        ]

        # STEP 3: Simulate the Runner starting the IDE.
        mock_runner_class.return_value.ensure_ide = AsyncMock(
            return_value={
                "sandbox_id": str(self.sandbox_id),
                "status": "running",
                "ready": True,
            }
        )

        # STEP 4: The changed generation must reject access.
        with self.assertRaises(PermissionError):
            asyncio.run(
                get_authorized_ide(
                    user=self.developer,
                    group_id=5,
                )
            )

        # STEP 5: Confirm authorization ran twice.
        self.assertEqual(
            mock_authorize.await_count,
            2,
        )

    @patch(
        "server_runner.ide_gateway.authorize_ide_request"
    )
    @patch(
        "server_runner.ide_gateway.CodeExecutionRunnerClient"
    )
    def test_membership_removed_during_startup_rejected(
        self,
        mock_runner_class,
        mock_authorize,
    ):
        """
        Reject access if the developer is removed during startup.
        """

        # STEP 1: The first authorization succeeds.
        # The second authorization rejects removed membership.
        mock_authorize.side_effect = [
            (12, 5, self.membership_generation),
            PermissionError(
                "Developer is no longer a group member."
            ),
        ]

        # STEP 2: Simulate a successfully started IDE.
        mock_runner_class.return_value.ensure_ide = AsyncMock(
            return_value={
                "sandbox_id": str(self.sandbox_id),
                "status": "running",
                "ready": True,
            }
        )

        # STEP 3: Access must be denied.
        with self.assertRaises(PermissionError):
            asyncio.run(
                get_authorized_ide(
                    user=self.developer,
                    group_id=5,
                )
            )

        # STEP 4: Confirm both authorization checks occurred.
        self.assertEqual(
            mock_authorize.await_count,
            2,
        )


if __name__ == "__main__":
    import unittest

    unittest.main()