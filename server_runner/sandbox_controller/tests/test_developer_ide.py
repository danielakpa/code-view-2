"""Tests for CodeView's persistent developer IDE service."""

import unittest

from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from server_runner.sandbox_controller.authorization import (
    SandboxIdentity,
)

from server_runner.sandbox_controller.command_executor import (
    CommandResult,
)

from server_runner.sandbox_controller.developer_ide import (
    DeveloperIDEService,
)


class TestDeveloperIDEService(unittest.TestCase):
    """Verify that IDE operations use approved Docker commands."""

    def setUp(self):
        """Prepare mocked dependencies for each test."""

        # Create a unique sandbox identifier.
        self.sandbox_id = uuid4()

        # Create a trusted identity for testing.
        self.identity = SandboxIdentity(
            sandbox_id=self.sandbox_id,
            developer_id=1,
            group_id=1,
        )

        # Example Docker container ID: 64 hexadecimal characters.
        self.container_id = "a" * 64

        # Example developer workspace.
        self.workspace_path = Path(
            "/var/lib/codeview/workspaces/developer_1/group_1"
        )

        # Mock the runtime so no real Docker commands are built.
        self.runtime = Mock()

        # Mock the executor so no real Docker commands run.
        self.executor = Mock()

        # Return a successful result by default.
        self.executor.execute.return_value = CommandResult(
            return_code=0,
            stdout="operation successful\n",
            stderr="",
            timed_out=False,
        )

        # Inject our mocked dependencies.
        self.service = DeveloperIDEService(
            runtime=self.runtime,
            executor=self.executor,
        )

    @patch(
        "server_runner.sandbox_controller.developer_ide.resolve_workspace"
    )
    def test_create_ide(self, mock_resolve_workspace):
        """Creating an IDE must use its trusted workspace."""

        # Return the expected workspace path.
        mock_resolve_workspace.return_value = self.workspace_path

        # Define the command returned by DockerRuntime.
        command = ["docker", "create", "--name", "test-container"]

        self.runtime.build_create_command.return_value = command

        # Request IDE creation.
        result = self.service.create_ide(self.identity)

        # Verify the correct workspace was resolved.
        mock_resolve_workspace.assert_called_once_with(
            self.identity
        )

        # Verify DockerRuntime received the correct values.
        self.runtime.build_create_command.assert_called_once_with(
            sandbox_id=self.sandbox_id,
            workspace_path=self.workspace_path,
        )

        # Verify the approved command was executed.
        self.executor.execute.assert_called_once_with(command)

        # Verify the result.
        self.assertTrue(result.success)
        self.assertEqual(result.output, "operation successful")
        self.assertEqual(result.error, "")

    def test_start_ide(self):
        """Starting an IDE must use the approved Docker command."""

        command = ["docker", "start", self.container_id]

        self.runtime.build_start_command.return_value = command

        result = self.service.start_ide(self.container_id)

        self.runtime.build_start_command.assert_called_once_with(
            self.container_id
        )

        self.executor.execute.assert_called_once_with(command)

        self.assertTrue(result.success)

    def test_stop_ide(self):
        """Stopping an IDE must use the approved Docker command."""

        command = ["docker", "stop", self.container_id]

        self.runtime.build_stop_command.return_value = command

        result = self.service.stop_ide(self.container_id)

        self.runtime.build_stop_command.assert_called_once_with(
            self.container_id
        )

        self.executor.execute.assert_called_once_with(command)

        self.assertTrue(result.success)

    def test_inspect_ide(self):
        """Inspecting an IDE must use the approved Docker command."""

        command = [
            "docker",
            "inspect",
            self.container_id,
        ]

        self.runtime.build_inspect_command.return_value = command

        result = self.service.inspect_ide(self.container_id)

        self.runtime.build_inspect_command.assert_called_once_with(
            self.container_id
        )

        self.executor.execute.assert_called_once_with(command)

        self.assertTrue(result.success)

    def test_docker_failure(self):
        """Docker command failures must not be reported as success."""

        command = ["docker", "start", self.container_id]

        self.runtime.build_start_command.return_value = command

        # Simulate a failed Docker command.
        self.executor.execute.return_value = CommandResult(
            return_code=1,
            stdout="",
            stderr="Container could not start.",
            timed_out=False,
        )

        result = self.service.start_ide(self.container_id)

        self.assertFalse(result.success)
        self.assertEqual(
            result.error,
            "Container could not start.",
        )

    def test_docker_timeout(self):
        """Timed-out Docker operations must not report success."""

        command = ["docker", "start", self.container_id]

        self.runtime.build_start_command.return_value = command

        # Simulate an operation exceeding its timeout.
        self.executor.execute.return_value = CommandResult(
            return_code=None,
            stdout="",
            stderr="",
            timed_out=True,
        )

        result = self.service.start_ide(self.container_id)

        self.assertFalse(result.success)

    def test_invalid_identity(self):
        """IDE creation must reject invalid identities."""

        with self.assertRaises((TypeError, ValueError)):
            self.service.create_ide(None)

        # Invalid requests must never execute Docker.
        self.executor.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()