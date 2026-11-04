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

        # Create a fake authenticated developer.
        self.developer = Mock()
        self.developer.pk = 12
        self.developer.is_authenticated = True

        # Create a fake private development group.
        self.group = Mock()
        self.group.pk = 5
        self.group.is_private = True

        # Create a valid sandbox UUID for testing.
        self.sandbox_id = uuid4()

    @patch("server_runner.ide_gateway.authorize_developer_ide_access")
    @patch("server_runner.ide_gateway.ProjectGroup.objects")
    def test_authorized_developer(
        self,
        mock_groups,
        mock_authorize,
    ):
        """Allow an authorized developer to access their IDE."""

        # Simulate finding the requested private group.
        mock_groups.filter.return_value.first.return_value = self.group

        # Execute the asynchronous authorization function.
        result = asyncio.run(
            authorize_ide_request(
                user=self.developer,
                group_id=5,
            )
        )

        # Confirm the correct developer and group IDs.
        self.assertEqual(result, (12, 5))

        # Confirm that existing authorization was called.
        mock_authorize.assert_called_once_with(
            user=self.developer,
            group=self.group,
            developer_id=12,
        )

    @patch("server_runner.ide_gateway.ProjectGroup.objects")
    def test_anonymous_developer_rejected(self, mock_groups):
        """Reject a developer who has not logged in."""

        self.developer.is_authenticated = False

        with self.assertRaises(PermissionError):
            asyncio.run(
                authorize_ide_request(
                    user=self.developer,
                    group_id=5,
                )
            )

        # Authentication must happen before database access.
        mock_groups.filter.assert_not_called()

    @patch("server_runner.ide_gateway.authorize_developer_ide_access")
    @patch("server_runner.ide_gateway.ProjectGroup.objects")
    def test_unauthorized_group_member_rejected(
        self,
        mock_groups,
        mock_authorize,
    ):
        """Reject a developer who lacks group permission."""

        mock_groups.filter.return_value.first.return_value = self.group

        # Simulate existing authorization denying access.
        mock_authorize.side_effect = PermissionError(
            "Developer is not an approved member."
        )

        with self.assertRaises(PermissionError):
            asyncio.run(
                authorize_ide_request(
                    user=self.developer,
                    group_id=5,
                )
            )

    @patch("server_runner.ide_gateway.authorize_developer_ide_access")
    @patch("server_runner.ide_gateway.ProjectGroup.objects")
    def test_nonexistent_group_rejected(
        self,
        mock_groups,
        mock_authorize,
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

        mock_authorize.assert_not_called()

    @patch("server_runner.ide_gateway.authorize_ide_request")
    @patch("server_runner.ide_gateway.CodeExecutionRunnerClient")
    def test_valid_sandbox_uuid(
        self,
        mock_runner_class,
        mock_authorize,
    ):
        """Accept a valid sandbox UUID from the trusted Runner."""

        # STEP 1: Simulate successful developer authorization.
        async def authorize(*args, **kwargs):
            return 12, 5

        mock_authorize.side_effect = authorize

        # STEP 2: Create an asynchronous mock for ensure_ide.
        mock_ensure_ide = AsyncMock(
            return_value={
                "sandbox_id": str(self.sandbox_id),
                "status": "running",
                "ready": True,
            }
        )

        # STEP 3: Attach the asynchronous mock to the Runner client.
        mock_runner_class.return_value.ensure_ide = mock_ensure_ide

        # STEP 4: Call the real gateway function.
        result = asyncio.run(
            get_authorized_ide(
                user=self.developer,
                group_id=5,
            )
        )

        # STEP 5: Verify the returned sandbox UUID.
        self.assertEqual(result, self.sandbox_id)

        # STEP 6: Verify the Runner was awaited with the correct IDs.
        mock_ensure_ide.assert_awaited_once_with(
            developer_id=12,
            group_id=5,
        )

    @patch("server_runner.ide_gateway.authorize_ide_request")
    @patch("server_runner.ide_gateway.CodeExecutionRunnerClient")
    def test_invalid_sandbox_uuid_rejected(
        self,
        mock_runner_class,
        mock_authorize,
    ):
        """Reject an invalid sandbox UUID returned by the Runner."""

        async def authorize(*args, **kwargs):
            return 12, 5

        async def ensure_ide(*args, **kwargs):
            return {
                "sandbox_id": "invalid-uuid",
                "status": "running",
                "ready": True,
            }

        mock_authorize.side_effect = authorize
        mock_runner_class.return_value.ensure_ide.side_effect = ensure_ide

        with self.assertRaises(RuntimeError):
            asyncio.run(
                get_authorized_ide(
                    user=self.developer,
                    group_id=5,
                )
            )

    @patch("server_runner.ide_gateway.authorize_ide_request")
    @patch("server_runner.ide_gateway.CodeExecutionRunnerClient")
    def test_ide_not_running_rejected(
        self,
        mock_runner_class,
        mock_authorize,
    ):
        """Reject a Runner response indicating the IDE is not running."""

        async def authorize(*args, **kwargs):
            return 12, 5

        async def ensure_ide(*args, **kwargs):
            return {
                "sandbox_id": str(self.sandbox_id),
                "status": "stopped",
                "ready": False,
            }

        mock_authorize.side_effect = authorize
        mock_runner_class.return_value.ensure_ide.side_effect = ensure_ide

        with self.assertRaises(RuntimeError):
            asyncio.run(
                get_authorized_ide(
                    user=self.developer,
                    group_id=5,
                )
            )