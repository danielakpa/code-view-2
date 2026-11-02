"""Manage persistent VS Code containers for CodeView developers."""

from dataclasses import dataclass
from uuid import UUID

from server_runner.sandbox_controller.authorization import (
    SandboxIdentity,
    validate_sandbox_identity,
)

from server_runner.sandbox_controller.command_executor import (
    CommandExecutor,
)

from server_runner.sandbox_controller.runtime import (
    DockerRuntime,
)

from server_runner.sandbox_controller.workspace import (
    resolve_workspace,
)


# ============================================================
# 1. IDE RESULT
# ============================================================

@dataclass(frozen=True)
class DeveloperIDEResult:
    """Represent the result of a Docker IDE operation."""

    success: bool
    output: str
    error: str


# ============================================================
# 2. IDE SERVICE
# ============================================================

class DeveloperIDEService:
    """Create, start, stop, and inspect developer IDE containers."""

    def __init__(
        self,
        runtime: DockerRuntime | None = None,
        executor: CommandExecutor | None = None,
    ):
        """Initialize the existing Docker components."""

        # Builds approved Docker commands.
        self.runtime = runtime or DockerRuntime()

        # Executes approved Docker commands.
        self.executor = executor or CommandExecutor()

    def _execute(self, command: list[str]) -> DeveloperIDEResult:
        """Execute a trusted Docker command."""

        # Run the command using the existing executor.
        result = self.executor.execute(command)

        # Convert the command result into an IDE result.
        return DeveloperIDEResult(
            success=(
                result.return_code == 0
                and not result.timed_out
            ),
            output=result.stdout.strip(),
            error=result.stderr.strip(),
        )

    def create_ide(
        self,
        identity: SandboxIdentity,
    ) -> DeveloperIDEResult:
        """Create a persistent VS Code container."""

        # Validate the trusted identity.
        validate_sandbox_identity(identity)

        # Find the developer's existing workspace.
        workspace_path = resolve_workspace(identity)

        # Build the approved Docker create command.
        command = self.runtime.build_create_command(
            sandbox_id=identity.sandbox_id,
            workspace_path=workspace_path,
        )

        # Create the Docker container.
        return self._execute(command)

    def start_ide(
        self,
        container_id: str,
    ) -> DeveloperIDEResult:
        """Start an existing VS Code container."""

        # Validate the ID and build the Docker command.
        command = self.runtime.build_start_command(
            container_id
        )

        # Start the container.
        return self._execute(command)

    def stop_ide(
        self,
        container_id: str,
    ) -> DeveloperIDEResult:
        """Stop an IDE without deleting its workspace."""

        # Build the approved Docker stop command.
        command = self.runtime.build_stop_command(
            container_id
        )

        # Stop the container.
        return self._execute(command)

    def inspect_ide(
        self,
        container_id: str,
    ) -> DeveloperIDEResult:
        """Inspect the current Docker container status."""

        # Build the approved Docker inspect command.
        command = self.runtime.build_inspect_command(
            container_id
        )

        # Inspect the container.
        return self._execute(command)