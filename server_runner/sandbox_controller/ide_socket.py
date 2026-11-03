"""Manage trusted Unix socket paths for CodeView developer IDEs."""

import os
import stat
import subprocess

from pathlib import Path
from uuid import UUID


# Store IDE sockets separately from developer workspaces.
IDE_SOCKET_ROOT = Path("/run/codeview/ide-sockets")

# The Linux user running code-server inside Docker.
DEVELOPER_UID = 10001


class IDESocketError(ValueError):
    """Raised when an IDE socket path is invalid."""


def get_ide_socket_directory(sandbox_id: UUID) -> Path:
    """Return the private socket directory for one sandbox."""

    # Only accept valid, nonzero UUID objects.
    if not isinstance(sandbox_id, UUID) or sandbox_id.int == 0:
        raise IDESocketError("Invalid sandbox ID.")

    # Generate a predictable directory name from the UUID.
    return IDE_SOCKET_ROOT / f"codeview-{sandbox_id.hex}"


def get_ide_socket_path(sandbox_id: UUID) -> Path:
    """Return the expected Unix socket path for one sandbox."""

    # The socket will be created by code-server.
    return get_ide_socket_directory(sandbox_id) / "ide.sock"


def prepare_ide_socket_directory(sandbox_id: UUID) -> Path:
    """
    Prepare a private directory for one developer IDE.

    The Controller owns the directory.

    An ACL grants the Docker developer UID permission
    to create its Unix socket inside the directory.
    """

    # Validate the sandbox ID and calculate its directory.
    socket_directory = get_ide_socket_directory(sandbox_id)

    try:
        # Inspect the socket root without following symlinks.
        root_info = os.lstat(IDE_SOCKET_ROOT)

        # Require a real directory owned by the Controller.
        if not stat.S_ISDIR(root_info.st_mode):
            raise IDESocketError(
                "IDE socket root must be a real directory."
            )

        if root_info.st_uid != os.getuid():
            raise IDESocketError(
                "IDE socket root has an unexpected owner."
            )

        if stat.S_IMODE(root_info.st_mode) != 0o700:
            raise IDESocketError(
                "IDE socket root has unsafe permissions."
            )

        # Create the sandbox-specific directory.
        socket_directory.mkdir(
            mode=0o700,
            exist_ok=True,
        )

        # Inspect the directory without following symlinks.
        directory_info = os.lstat(socket_directory)

        if not stat.S_ISDIR(directory_info.st_mode):
            raise IDESocketError(
                "IDE socket path must be a real directory."
            )

        if directory_info.st_uid != os.getuid():
            raise IDESocketError(
                "IDE socket directory has an unexpected owner."
            )

        # Reject access by the group or other users.
        if (
            directory_info.st_mode & stat.S_IRWXG
            or directory_info.st_mode & stat.S_IRWXO
        ):
            raise IDESocketError(
                "IDE socket directory has unsafe permissions."
            )

        # Grant the Docker developer UID access using Linux ACLs.
        result = subprocess.run(
            [
                "setfacl",
                "-m",
                f"u:{DEVELOPER_UID}:rwx",
                str(socket_directory),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        if result.returncode != 0:
            raise IDESocketError(
                "Could not configure IDE socket permissions."
            )

    except (OSError, subprocess.TimeoutExpired) as error:
        raise IDESocketError(
            "Could not prepare IDE socket directory."
        ) from error

    return socket_directory