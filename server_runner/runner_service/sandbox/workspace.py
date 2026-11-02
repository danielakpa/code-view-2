"""Manage persistent, private workspaces for CodeView developers."""

import os
from pathlib import Path


# Root directory where CodeView stores developer environments.
#
# This directory lives on the host server, not inside Docker.
# Therefore, stopping or removing a container does not delete
# the developer's saved files.
WORKSPACE_ROOT = Path(
    os.environ.get(
        "CODEVIEW_WORKSPACE_ROOT",
        "/var/lib/codeview/workspaces",
    )
)


# Directories used to persist a developer's environment.
#
# projects:
#     Stores source code, Git repositories, and local branches.
#
# extensions:
#     Stores installed code-server extensions.
#
# config:
#     Stores code-server settings and user configuration.
#
# cache:
#     Stores reusable application and package caches.
#
# local:
#     Stores additional user-level application data.
#
# These directories will be mounted into the developer's
# Docker container during a later development step.
WORKSPACE_DIRECTORIES = (
    "projects",
    "extensions",
    "config",
    "cache",
    "local",
)


def validate_workspace_id(value):
    """Validate a developer or group ID."""

    # IDs must be positive integers.
    # Reject booleans because bool is a subclass of int in Python.
    if type(value) is not int or value <= 0:
        raise ValueError(
            "Workspace IDs must be positive integers."
        )

    return value


def get_workspace_path(developer_id, group_id):
    """Return the private workspace path for a developer and group."""

    # Validate the developer ID.
    developer_id = validate_workspace_id(developer_id)

    # Validate the group ID.
    group_id = validate_workspace_id(group_id)

    # Each developer gets a separate directory.
    #
    # Even developers in the same group have different
    # workspaces, preventing accidental file sharing.
    return (
        WORKSPACE_ROOT
        / f"developer_{developer_id}"
        / f"group_{group_id}"
    )


def get_workspace_directory(developer_id, group_id, directory_name):
    """Return the path of a supported workspace subdirectory."""

    # Only allow directory names defined by CodeView.
    # This prevents callers from requesting arbitrary paths.
    if directory_name not in WORKSPACE_DIRECTORIES:
        raise ValueError(
            "Invalid workspace directory."
        )

    # Get the developer's private workspace.
    workspace_path = get_workspace_path(
        developer_id,
        group_id,
    )

    # Return the requested directory path.
    return workspace_path / directory_name


def create_workspace(developer_id, group_id):
    """Create persistent directories for a developer's workspace."""

    # Build the validated workspace path.
    workspace_path = get_workspace_path(
        developer_id,
        group_id,
    )

    # Create the developer's main workspace directory.
    #
    # exist_ok=True means existing workspaces are reused.
    # This is important because developer files must survive
    # container restarts.
    workspace_path.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o700,
    )

    # Restrict access to the directory owner.
    workspace_path.chmod(0o700)

    # Create all supported persistent directories.
    for directory_name in WORKSPACE_DIRECTORIES:

        # Build the subdirectory path.
        directory_path = workspace_path / directory_name

        # Create it if it does not already exist.
        directory_path.mkdir(
            exist_ok=True,
            mode=0o700,
        )

        # Restrict access to its owner.
        directory_path.chmod(0o700)

    # Return the main workspace path.
    #
    # We preserve the original return value because
    # other CodeView components already use this function.
    return workspace_path