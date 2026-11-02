"""Manage persistent, private workspaces for CodeView developers."""

import os
import subprocess
from pathlib import Path


# ============================================================
# 1. WORKSPACE CONFIGURATION
# ============================================================

# Root directory for persistent developer workspaces.
WORKSPACE_ROOT = Path(
    os.environ.get(
        "CODEVIEW_WORKSPACE_ROOT",
        "/var/lib/codeview/workspaces",
    )
)

# Linux user running the developer Docker container.
DEVELOPER_UID = 10001

# Linux user running the CodeView workspace manager.
HOST_UID = os.getuid()

# Directories whose contents must survive container restarts.
WORKSPACE_DIRECTORIES = (
    "projects",
    "extensions",
    "config",
    "cache",
    "local",
)


# ============================================================
# 2. VALIDATE WORKSPACE IDS
# ============================================================

def validate_workspace_id(value):
    """Accept only positive integer IDs."""

    # Reject booleans, strings, zero, and negative numbers.
    if type(value) is not int or value <= 0:
        raise ValueError(
            "Workspace IDs must be positive integers."
        )

    return value


# ============================================================
# 3. BUILD WORKSPACE PATHS
# ============================================================

def get_workspace_path(developer_id, group_id):
    """Return a developer's private workspace path."""

    developer_id = validate_workspace_id(developer_id)
    group_id = validate_workspace_id(group_id)

    return (
        WORKSPACE_ROOT
        / f"developer_{developer_id}"
        / f"group_{group_id}"
    )


def get_workspace_directory(developer_id, group_id, directory_name):
    """Return a supported workspace subdirectory."""

    if directory_name not in WORKSPACE_DIRECTORIES:
        raise ValueError(
            "Invalid workspace directory."
        )

    return (
        get_workspace_path(developer_id, group_id)
        / directory_name
    )


# ============================================================
# 4. ACL HELPERS
# ============================================================

def set_access_acl(directory_path, uid, permissions):
    """Grant a specific Linux user access to a directory."""

    subprocess.run(
        [
            "setfacl",
            "-m",
            f"u:{uid}:{permissions}",
            str(directory_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def set_default_acl(directory_path):
    """
    Configure permissions inherited by newly created files.

    Both the host workspace manager and Docker developer
    receive access to new files and directories.
    """

    subprocess.run(
        [
            "setfacl",
            "-m",
            (
                f"d:u:{HOST_UID}:rwx,"
                f"d:u:{DEVELOPER_UID}:rwx"
            ),
            str(directory_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def prepare_directory(directory_path):
    """
    Create a directory and configure its ACL permissions.

    Existing directories are reused without changing
    permissions recursively.
    """

    directory_path = Path(directory_path)

    # Create the directory if it doesn't already exist.
    directory_path.mkdir(
        exist_ok=True,
        mode=0o700,
    )

    # Grant the host user and Docker developer access.
    set_access_acl(
        directory_path,
        HOST_UID,
        "rwx",
    )

    set_access_acl(
        directory_path,
        DEVELOPER_UID,
        "rwx",
    )

    # Ensure new files inherit access permissions.
    set_default_acl(directory_path)


# ============================================================
# 5. CREATE PERSISTENT WORKSPACE
# ============================================================

def create_workspace(developer_id, group_id):
    """
    Create or reuse a persistent developer workspace.

    Existing source code and configuration are preserved.
    """

    workspace_path = get_workspace_path(
        developer_id,
        group_id,
    )

    developer_path = workspace_path.parent

    # Create the workspace root if needed.
    WORKSPACE_ROOT.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o700,
    )

    # Create the developer's parent directory.
    developer_path.mkdir(
        exist_ok=True,
        mode=0o700,
    )

    # Allow Docker to traverse the parent directories.
    # Traversal does not grant permission to list files.
    set_access_acl(
        WORKSPACE_ROOT,
        DEVELOPER_UID,
        "--x",
    )

    set_access_acl(
        developer_path,
        DEVELOPER_UID,
        "--x",
    )

    # Prepare the main workspace.
    prepare_directory(workspace_path)

    # Prepare persistent subdirectories.
    for directory_name in WORKSPACE_DIRECTORIES:

        directory_path = workspace_path / directory_name

        prepare_directory(directory_path)

    # Preserve the original return type expected by the Runner.
    return workspace_path