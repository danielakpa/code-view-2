"""Tests for the CodeView Docker runtime.

These tests verify that Docker commands are built correctly.

They do not start real Docker containers.
"""

import tempfile
import unittest

from pathlib import Path
from uuid import UUID, uuid4

from server_runner.sandbox_controller.runtime import (
    DEVELOPER_IMAGE,
    DockerRuntime,
    SandboxRuntimeError,
)


class TestDockerRuntime(unittest.TestCase):
    """Test secure Docker command construction."""

    def setUp(self):
        """Create the Docker runtime before every test."""

        self.runtime = DockerRuntime()

    def create_test_workspace(self, directory):
        """Create the persistent directories expected by CodeView 2.0.

        This helper gives every test a realistic developer workspace.
        """

        # Convert the temporary directory into a Path object.
        workspace = Path(directory)

        # Create the persistent directories used by code-server.
        for directory_name in (
            "projects",
            "extensions",
            "config",
            "cache",
            "local",
        ):
            (workspace / directory_name).mkdir(
                exist_ok=True,
            )

        # Return the workspace to the test.
        return workspace

    def test_create_command(self):
        """Verify that developer containers retain security restrictions."""

        with tempfile.TemporaryDirectory() as directory:

            # Generate a valid sandbox ID.
            sandbox_id = uuid4()

            # Create a realistic persistent developer workspace.
            workspace = self.create_test_workspace(directory)

            # Build the Docker creation command.
            command = self.runtime.build_create_command(
                sandbox_id,
                workspace,
            )

            # Verify that Docker creates a container.
            self.assertEqual(
                command[0:2],
                ["docker", "create"],
            )

            # Verify resource limits and security restrictions.
            self.assertIn("--memory", command)
            self.assertIn("--cpus", command)
            self.assertIn("--pids-limit", command)

            # Networking remains disabled until we add
            # private IDE networking and authentication.
            self.assertIn("--network", command)
            self.assertEqual(
                command[command.index("--network") + 1],
                "none",
            )

            # Verify that Linux capabilities are removed.
            self.assertIn("--cap-drop", command)
            self.assertIn("ALL", command)

            # Verify the root filesystem is read-only.
            self.assertIn("--read-only", command)

            # Verify that the container uses our non-root user.
            self.assertIn("--user", command)
            self.assertEqual(
                command[command.index("--user") + 1],
                "10001:10001",
            )

            # Verify the approved developer image.
            self.assertIn(DEVELOPER_IMAGE, command)

            # The Dockerfile now starts code-server.
            # We must not override its CMD with sleep infinity.
            self.assertEqual(
                command[-1],
                DEVELOPER_IMAGE,
            )

            self.assertNotIn("sleep", command)
            self.assertNotIn("infinity", command)

    def test_persistent_workspace_mounts(self):
        """Verify that developer files and editor data are persistent."""

        with tempfile.TemporaryDirectory() as directory:

            # Create the required workspace structure.
            workspace = self.create_test_workspace(directory)

            # Build the Docker command.
            command = self.runtime.build_create_command(
                uuid4(),
                workspace,
            )

            # Extract every Docker mount specification.
            mounts = [
                command[index + 1]
                for index, value in enumerate(command[:-1])
                if value == "--mount"
            ]

            # Developer project files remain available at /workspace.
            self.assertIn(
                (
                    f"type=bind,source={workspace},"
                    "target=/workspace"
                ),
                mounts,
            )

            # Code-server's user data and extensions are persistent.
            self.assertIn(
                (
                    f"type=bind,source={workspace / 'local'},"
                    "target=/home/developer/.local"
                ),
                mounts,
            )

            # Editor configuration survives container restarts.
            self.assertIn(
                (
                    f"type=bind,source={workspace / 'config'},"
                    "target=/home/developer/.config"
                ),
                mounts,
            )

            # Cached application data survives container restarts.
            self.assertIn(
                (
                    f"type=bind,source={workspace / 'cache'},"
                    "target=/home/developer/.cache"
                ),
                mounts,
            )

    def test_missing_persistent_directory_is_rejected(self):
        """Reject a workspace missing required persistent directories."""

        with tempfile.TemporaryDirectory() as directory:

            # This workspace is intentionally empty.
            workspace = Path(directory)

            # The runtime must reject incomplete workspace storage.
            with self.assertRaisesRegex(
                SandboxRuntimeError,
                "Persistent workspace directory missing",
            ):
                self.runtime.build_create_command(
                    uuid4(),
                    workspace,
                )

    def test_create_command_rejects_invalid_id(self):
        """Reject an invalid sandbox ID."""

        with tempfile.TemporaryDirectory() as directory:

            workspace = Path(directory)

            with self.assertRaises(SandboxRuntimeError):
                self.runtime.build_create_command(
                    "invalid",
                    workspace,
                )

    def test_create_command_rejects_zero_uuid(self):
        """Reject an all-zero sandbox UUID."""

        with tempfile.TemporaryDirectory() as directory:

            workspace = Path(directory)

            with self.assertRaises(SandboxRuntimeError):
                self.runtime.build_create_command(
                    UUID(int=0),
                    workspace,
                )

    def test_create_command_rejects_missing_workspace(self):
        """Reject a workspace that does not exist."""

        with self.assertRaises(SandboxRuntimeError):
            self.runtime.build_create_command(
                uuid4(),
                Path("/nonexistent-codeview-workspace"),
            )

    def test_create_command_rejects_relative_workspace(self):
        """Reject relative workspace paths."""

        with self.assertRaisesRegex(
            SandboxRuntimeError,
            "Workspace path must be absolute",
        ):
            self.runtime.build_create_command(
                uuid4(),
                Path("relative-workspace"),
            )

    def test_start_command(self):
        """Build a command to start an existing container."""

        container_id = "a" * 64

        command = self.runtime.build_start_command(
            container_id,
        )

        self.assertEqual(
            command,
            ["docker", "start", container_id],
        )

    def test_stop_command(self):
        """Build a command to stop an existing container."""

        container_id = "b" * 64

        command = self.runtime.build_stop_command(
            container_id,
        )

        self.assertEqual(
            command,
            ["docker", "stop", "--time", "10", container_id],
        )

    def test_inspect_command(self):
        """Build a command to inspect container status."""

        container_id = "c" * 64

        command = self.runtime.build_inspect_command(
            container_id,
        )

        self.assertEqual(
            command,
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}}",
                container_id,
            ],
        )

    def test_invalid_container_id(self):
        """Reject malformed Docker container IDs."""

        with self.assertRaises(SandboxRuntimeError):
            self.runtime.build_start_command("invalid")

    def test_docker_socket_is_not_mounted(self):
        """Ensure developers cannot access the host Docker socket."""

        with tempfile.TemporaryDirectory() as directory:

            # Create a complete workspace.
            workspace = self.create_test_workspace(directory)

            # Build the Docker creation command.
            command = self.runtime.build_create_command(
                uuid4(),
                workspace,
            )

            # Convert the command into text for inspection.
            command_text = " ".join(command)

            # The Docker socket must never be mounted.
            self.assertNotIn(
                "/var/run/docker.sock",
                command_text,
            )


if __name__ == "__main__":
    unittest.main()