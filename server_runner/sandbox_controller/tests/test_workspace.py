"""Test trusted workspace resolution for CodeView."""

import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from server_runner.sandbox_controller.authorization import (
    SandboxIdentity,
)

from server_runner.sandbox_controller.workspace import (
    SandboxWorkspaceError,
    resolve_workspace,
)


class TestSandboxWorkspace(unittest.TestCase):
    """Test workspace validation and security."""

    def setUp(self):
        """Prepare a temporary workspace for each test."""

        # Create a temporary root directory.
        self.temp_directory = tempfile.TemporaryDirectory()

        # Register cleanup after each test.
        self.addCleanup(self.temp_directory.cleanup)

        # Get the temporary root path.
        self.root = Path(self.temp_directory.name)

        # Create a trusted sandbox identity.
        self.identity = SandboxIdentity(
            sandbox_id=uuid4(),
            developer_id=1,
            group_id=2,
        )

        # Build the expected developer workspace.
        self.workspace = (
            self.root
            / "developer_1"
            / "group_2"
        )

        # Replace the real workspace root during testing.
        root_patch = patch(
            "server_runner.sandbox_controller.workspace.WORKSPACE_ROOT",
            self.root,
        )

        root_patch.start()
        self.addCleanup(root_patch.stop)

        # The imported get_workspace_path() uses its own module's root.
        runner_root_patch = patch(
            "server_runner.runner_service.sandbox.workspace.WORKSPACE_ROOT",
            self.root,
        )

        runner_root_patch.start()
        self.addCleanup(runner_root_patch.stop)

    def test_valid_workspace(self):
        """An existing valid workspace should be accepted."""

        # Create the workspace.
        self.workspace.mkdir(parents=True)

        # Resolve the workspace.
        result = resolve_workspace(self.identity)

        # Verify the correct path is returned.
        self.assertEqual(
            result,
            self.workspace.resolve(),
        )

    def test_missing_workspace(self):
        """A missing workspace must be rejected."""

        # Do not create the workspace.
        with self.assertRaises(SandboxWorkspaceError):
            resolve_workspace(self.identity)

    def test_invalid_identity(self):
        """An invalid sandbox identity must be rejected."""

        with self.assertRaises(ValueError):
            resolve_workspace("invalid identity")

    def test_workspace_escape_is_rejected(self):
        """A symlink pointing outside the root must be rejected."""

        # Create a separate directory outside the workspace root.
        with tempfile.TemporaryDirectory() as outside_directory:

            outside_path = Path(outside_directory)

            # Create the developer's parent directory.
            self.workspace.parent.mkdir(parents=True)

            # Make the workspace a symlink to an outside directory.
            self.workspace.symlink_to(
                outside_path,
                target_is_directory=True,
            )

            # The resolver must reject the escaped path.
            with self.assertRaises(SandboxWorkspaceError):
                resolve_workspace(self.identity)

    def test_workspace_must_be_directory(self):
        """A regular file cannot be used as a workspace."""

        # Create the parent directory.
        self.workspace.parent.mkdir(parents=True)

        # Create a file where the workspace directory should be.
        self.workspace.write_text("not a directory")

        # Reject the invalid workspace.
        with self.assertRaises(SandboxWorkspaceError):
            resolve_workspace(self.identity)


    def test_cross_developer_symlink_is_rejected(self):
        """A workspace cannot redirect to another developer's workspace."""

        # Create Developer 2's workspace inside the same root.
        other_workspace = (
            self.root
            / "developer_2"
            / "group_2"
        )

        other_workspace.mkdir(parents=True)

        # Create Developer 1's parent directory.
        self.workspace.parent.mkdir(parents=True)

        # Redirect Developer 1's workspace to Developer 2's workspace.
        self.workspace.symlink_to(
            other_workspace,
            target_is_directory=True,
        )

        # CodeView must reject the redirected workspace.
        with self.assertRaises(SandboxWorkspaceError):
            resolve_workspace(self.identity)

if __name__ == "__main__":
    unittest.main()