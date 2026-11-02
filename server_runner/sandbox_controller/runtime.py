"""Docker runtime for CodeView developer sandboxes.

This module prepares Docker commands.

IMPORTANT:
Docker commands are NOT executed in this step.
"""

import re
from pathlib import Path
from uuid import UUID

from server_runner.sandbox_controller.security import (
    get_controller_security_config,
)


# Use a fixed developer image rather than accepting arbitrary images.
DEVELOPER_IMAGE = "ghcr.io/danielakpa/codeview-developer:dev"

# Docker container IDs contain 64 hexadecimal characters.
CONTAINER_ID_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class SandboxRuntimeError(RuntimeError):
    """Raised when sandbox runtime configuration is invalid."""


class DockerRuntime:
    """Build restricted Docker commands for developer sandboxes."""

    def __init__(self):
        """Load the controller's security configuration."""

        self.security = get_controller_security_config()

    def build_create_command(
        self,
        sandbox_id: UUID,
        workspace_path: Path,
    ) -> list[str]:
        """Build a persistent developer IDE container creation command.

        The existing workspace mount stays at /workspace for compatibility
        with the older execution system. Editor data uses separate mounts.
        """

        # Reject invalid sandbox identifiers.
        if not isinstance(sandbox_id, UUID) or sandbox_id.int == 0:
            raise SandboxRuntimeError("Invalid sandbox ID.")

        # Never accept relative paths or missing workspaces.
        if not isinstance(workspace_path, Path):
            raise SandboxRuntimeError("Invalid workspace path.")

        if not workspace_path.is_absolute():
            raise SandboxRuntimeError(
                "Workspace path must be absolute."
            )

        if not workspace_path.is_dir():
            raise SandboxRuntimeError(
                "Workspace directory does not exist."
            )

        # The workspace manager creates these persistent directories.
        # Do not silently create them here: the controller should not
        # change host storage ownership or permissions.
        persistent_mounts = (
            ("local", "/home/developer/.local"),
            ("config", "/home/developer/.config"),
            ("cache", "/home/developer/.cache"),
        )

        for directory_name, _ in persistent_mounts:
            directory_path = workspace_path / directory_name

            if not directory_path.is_dir():
                raise SandboxRuntimeError(
                    f"Persistent workspace directory missing: {directory_name}."
                )

        # Give every sandbox a unique Docker container name.
        container_name = f"codeview-{sandbox_id.hex}"

        # Build the Docker command.
        command = [
            "docker",
            "create",

            # Assign a unique container name.
            "--name",
            container_name,

            # Limit RAM usage.
            "--memory",
            f"{self.security.memory_limit_mb}m",

            # Limit CPU usage.
            "--cpus",
            str(self.security.cpu_limit),

            # Limit the number of processes.
            "--pids-limit",
            str(self.security.process_limit),

            # Keep network access disabled until we implement a private
            # IDE network and an authenticated Django reverse proxy.
            "--network",
            "none",

            # Prevent privilege escalation.
            "--security-opt",
            "no-new-privileges",

            # Remove unnecessary Linux capabilities.
            "--cap-drop",
            "ALL",

            # Keep the container's root filesystem read-only.
            "--read-only",

            # Run as the non-root developer user.
            "--user",
            "10001:10001",

            # Provide temporary writable storage.
            "--tmpfs",
            "/tmp:rw,exec,nosuid,nodev,size=256m",

            # Mount the developer's persistent workspace.
            #
            # Existing CodeView components expect /workspace,
            # so we preserve that location.
            "--mount",
            (
                "type=bind,"
                f"source={workspace_path},"
                "target=/workspace"
            ),
        ]

        # Mount persistent application directories.
        #
        # These preserve code-server settings, extensions,
        # and caches when containers are stopped or removed.
        #
        # Host directories must be writable by UID 10001
        # before the container is started.
        for directory_name, container_path in persistent_mounts:
            command.extend([
                "--mount",
                (
                    "type=bind,"
                    f"source={workspace_path / directory_name},"
                    f"target={container_path}"
                ),
            ])

        # Finish building the Docker command.
        command.extend([
            # Set the working directory.
            "--workdir",
            "/workspace",

            # Use the approved CodeView developer image.
            DEVELOPER_IMAGE,

            # Do not override the Dockerfile CMD.
            # The image now starts code-server automatically.
        ])

        return command

    def build_start_command(self, container_id: str) -> list[str]:
        """Build the command to start an existing container."""

        self._validate_container_id(container_id)

        return [
            "docker",
            "start",
            container_id,
        ]

    def build_stop_command(self, container_id: str) -> list[str]:
        """Build the command to stop an existing container."""

        self._validate_container_id(container_id)

        return [
            "docker",
            "stop",
            "--time",
            "10",
            container_id,
        ]

    def build_inspect_command(self, container_id: str) -> list[str]:
        """Build the command to inspect container status."""

        self._validate_container_id(container_id)

        return [
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}}",
            container_id,
        ]

    def _validate_container_id(self, container_id: str):
        """Reject invalid Docker container identifiers."""

        if (
            not isinstance(container_id, str)
            or CONTAINER_ID_PATTERN.fullmatch(container_id) is None
        ):
            raise SandboxRuntimeError(
                "Invalid Docker container ID."
            )

        return True

    def build_temporary_run_command(
        self,
        sandbox_id: UUID,
        workspace_path: Path,
        language: str,
    ) -> list[str]:
        """Build a restricted, temporary Docker execution command."""

        # Validate the sandbox identifier.
        if not isinstance(sandbox_id, UUID) or sandbox_id.int == 0:
            raise SandboxRuntimeError("Invalid sandbox ID.")

        # Validate the workspace.
        if (
            not isinstance(workspace_path, Path)
            or not workspace_path.is_absolute()
            or not workspace_path.is_dir()
        ):
            raise SandboxRuntimeError("Invalid workspace path.")

        # Only permit predefined language commands.
        allowed_commands = {
            "python": ["python3", "main.py"],
            "go": ["go", "run", "main.go"],
            "javascript": ["node", "main.js"],
        }

        if language not in allowed_commands:
            raise SandboxRuntimeError(
                "Unsupported programming language."
            )

        # Give each execution a unique container name.
        from uuid import uuid4

        container_name = (
            f"codeview-run-{sandbox_id.hex}-{uuid4().hex[:12]}"
        )

        # Build the restricted Docker command.
        return [
            "docker",
            "run",

            # Automatically remove the container when it exits.
            "--rm",

            # Assign a unique container name.
            "--name",
            container_name,

            # Limit memory.
            "--memory",
            f"{self.security.memory_limit_mb}m",

            # Limit CPU.
            "--cpus",
            str(self.security.cpu_limit),

            # Limit processes.
            "--pids-limit",
            str(self.security.process_limit),

            # Disable networking.
            "--network",
            "none",

            # Prevent privilege escalation.
            "--security-opt",
            "no-new-privileges",

            # Remove Linux capabilities.
            "--cap-drop",
            "ALL",

            # Make the root filesystem read-only.
            "--read-only",

            # Use a non-root user.
            "--user",
            "10001:10001",

            # Provide temporary writable storage.
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",

            # Mount the workspace read-only for Run mode.
            "--mount",
            (
                "type=bind,"
                f"source={workspace_path},"
                "target=/workspace,"
                "readonly"
            ),

            # Run from the workspace.
            "--workdir",
            "/workspace",

            # Use the approved developer image.
            DEVELOPER_IMAGE,

            # Execute the selected language.
            *allowed_commands[language],
        ]

    def build_temporary_create_command(
        self,
        sandbox_id: UUID,
        workspace_path: Path,
        language: str,
        container_name: str,
    ) -> list[str]:
        """Build a command to create a temporary execution container."""

        # Validate the sandbox ID.
        if not isinstance(sandbox_id, UUID) or sandbox_id.int == 0:
            raise SandboxRuntimeError("Invalid sandbox ID.")

        # Validate the workspace directory.
        if (
            not isinstance(workspace_path, Path)
            or not workspace_path.is_absolute()
            or not workspace_path.is_dir()
        ):
            raise SandboxRuntimeError("Invalid workspace path.")

        # Require a container name generated by our controller.
        expected_prefix = f"codeview-run-{sandbox_id.hex}-"

        if (
            not isinstance(container_name, str)
            or not container_name.startswith(expected_prefix)
            or not re.fullmatch(
                r"codeview-run-[a-f0-9]{32}-[a-f0-9]{12}",
                container_name,
            )
        ):
            raise SandboxRuntimeError("Invalid container name.")

        # Use fixed commands for supported languages.
        allowed_commands = {
            "python": ["python3", "main.py"],
            "go": ["go", "run", "main.go"],
            "javascript": ["node", "main.js"],
        }

        if language not in allowed_commands:
            raise SandboxRuntimeError(
                "Unsupported programming language."
            )

        return [
            "docker",
            "create",

            "--name",
            container_name,

            "--memory",
            f"{self.security.memory_limit_mb}m",

            "--cpus",
            str(self.security.cpu_limit),

            "--pids-limit",
            str(self.security.process_limit),

            "--network",
            "none",

            "--security-opt",
            "no-new-privileges",

            "--cap-drop",
            "ALL",

            "--read-only",

            "--user",
            "10001:10001",

            "--tmpfs",
            "/tmp:rw,exec,nosuid,nodev,size=512m",

            # Give Go a writable location for its compilation cache.
            "--env",
            "GOCACHE=/opt/codeview/go-cache",

            # Give Go a writable location for temporary compilation files.
            "--env",
            "GOTMPDIR=/tmp",

            # Prevent modifications to the developer's source files.
            "--mount",
            (
                "type=bind,"
                f"source={workspace_path},"
                "target=/workspace,"
                "readonly"
            ),

            "--workdir",
            "/workspace",

            DEVELOPER_IMAGE,

            *allowed_commands[language],
        ]

    def build_temporary_start_command(
        self,
        container_id: str,
    ) -> list[str]:
        """Start a temporary container and attach to its output."""

        self._validate_container_id(container_id)

        return [
            "docker",
            "start",
            "--attach",
            container_id,
        ]

    def build_temporary_kill_command(
        self,
        container_id: str,
    ) -> list[str]:
        """Immediately terminate a temporary container."""

        self._validate_container_id(container_id)

        return [
            "docker",
            "kill",
            container_id,
        ]

    def build_temporary_remove_command(
        self,
        container_id: str,
    ) -> list[str]:
        """Force-remove a temporary container."""

        self._validate_container_id(container_id)

        return [
            "docker",
            "rm",
            "--force",
            container_id,
        ]

    def build_run_command(
        self,
        container_id: str,
        language: str,
        filename: str,
    ) -> list[str]:
        """Build a command to run code inside a Docker sandbox."""

        # Validate the Docker container ID.
        self._validate_container_id(container_id)

        # Only allow supported languages and fixed filenames.
        allowed_commands = {
            "python": ("python3", "main.py"),
            "go": ("go", "run", "main.go"),
            "javascript": ("node", "main.js"),
        }

        if language not in allowed_commands:
            raise SandboxRuntimeError(
                "Unsupported programming language."
            )

        # Ensure the requested filename matches the language.
        expected_filename = allowed_commands[language][-1]

        if filename != expected_filename:
            raise SandboxRuntimeError(
                "Invalid program filename."
            )

        # Build the Docker command without using a shell.
        return [
            "docker",
            "exec",
            "--workdir",
            "/workspace",
            container_id,
            *allowed_commands[language],
        ]

    def build_temporary_inspect_command(
        self,
        container_name: str,
    ) -> list[str]:
        """Build a command to check whether a container still exists."""

        # Only accept names belonging to CodeView Run containers.
        if (
            not isinstance(container_name, str)
            or re.fullmatch(
                r"codeview-run-[a-f0-9]{32}-[a-f0-9]{12}",
                container_name,
            ) is None
        ):
            raise SandboxRuntimeError(
                "Invalid temporary container name."
            )

        return [
            "docker",
            "container",
            "inspect",
            container_name,
        ]

    def build_temporary_list_command(
        self,
        container_name: str,
    ) -> list[str]:
        """List containers matching a CodeView temporary container name."""

        if (
            not isinstance(container_name, str)
            or re.fullmatch(
                r"codeview-run-[a-f0-9]{32}-[a-f0-9]{12}",
                container_name,
            ) is None
        ):
            raise SandboxRuntimeError(
                "Invalid temporary container name."
            )

        return [
            "docker",
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            f"name=^/{container_name}$",
            "--format",
            "{{.Names}}",
        ]