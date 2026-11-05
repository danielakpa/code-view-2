"""Resolve trusted workspace paths for CodeView sandbox execution."""

from pathlib import Path

from server_runner.runner_service.sandbox.workspace import (
    WORKSPACE_ROOT,
    get_workspace_path,
)

from server_runner.sandbox_controller.authorization import (
    SandboxIdentity,
    validate_sandbox_identity,
)


class SandboxWorkspaceError(ValueError):
    """Raised when a sandbox workspace is invalid."""


def resolve_workspace(identity: SandboxIdentity) -> Path:
    """
    Resolve an existing workspace using a trusted sandbox identity.

    IMPORTANT:
    The identity must come from an authenticated, authorized
    server-side workflow, not directly from an API request.
    """

    # Validate the trusted sandbox identity structure.
    validate_sandbox_identity(identity)

    # Calculate the expected developer workspace.
    workspace_path = get_workspace_path(
        developer_id=identity.developer_id,
        group_id=identity.group_id,
    )

    # Resolve the configured workspace root.
    root = WORKSPACE_ROOT.resolve(strict=True)

    # Resolve the workspace without creating it.
    try:
        resolved_path = workspace_path.resolve(strict=True)

    except (OSError, RuntimeError) as error:
        raise SandboxWorkspaceError(
            "Developer workspace does not exist or cannot be resolved."
        ) from error

    # ========================================
    # VERIFY WORKSPACE PATH ISOLATION
    # ========================================

    # Ensure the workspace stays inside the configured root.
    if not resolved_path.is_relative_to(root):
        raise SandboxWorkspaceError(
            "Workspace path escapes the configured workspace root."
        )

    # Build the expected path from the trusted developer
    # and group IDs, without following symbolic links.
    expected_path = (
        root
        / f"developer_{identity.developer_id}"
        / f"group_{identity.group_id}"
    )

    # Reject paths redirected to another workspace.
    if resolved_path != expected_path:
        raise SandboxWorkspaceError(
            "Workspace path does not match sandbox identity."
        )

    # Reject files and other non-directory paths.
    if not resolved_path.is_dir():
        raise SandboxWorkspaceError(
            "Developer workspace is not a directory."
        )

    # Return the validated workspace location.
    return resolved_path