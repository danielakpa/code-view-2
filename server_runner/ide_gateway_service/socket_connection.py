"""Manage connections to CodeView IDE Unix sockets."""

import os
import stat

from pathlib import Path
from uuid import UUID

import httpx

from server_runner.ide_gateway_service.config import (
    get_gateway_settings,
)


def get_ide_socket_path(sandbox_id: UUID) -> Path:
    """
    Build the Unix socket path for an authorized sandbox.

    The sandbox ID must come from a verified browser session,
    never directly from an untrusted browser parameter.
    """

    # STEP 1: Validate the sandbox identity.
    if not isinstance(sandbox_id, UUID) or sandbox_id.int == 0:
        raise ValueError("Invalid sandbox identity.")

    # STEP 2: Read the Gateway configuration.
    settings = get_gateway_settings()

    # STEP 3: Identify the configured socket directory.
    socket_root = Path(settings.socket_root)

    # STEP 4: Build the sandbox-specific directory name.
    sandbox_directory = f"codeview-{sandbox_id.hex}"

    # STEP 5: Build the complete Unix socket path.
    socket_path = (
        socket_root
        / sandbox_directory
        / "ide.sock"
    )

    return socket_path


def validate_ide_socket(socket_path: Path) -> None:
    """
    Verify that the IDE endpoint is a real Unix socket.

    Reject missing paths, symbolic links, and regular files.
    """

    # STEP 1: Check the path without following symbolic links.
    socket_info = socket_path.lstat()

    # STEP 2: Reject anything that is not a Unix socket.
    if not stat.S_ISSOCK(socket_info.st_mode):
        raise ValueError(
            "IDE endpoint is not a Unix socket."
        )

    # STEP 3: Reject symbolic-link parent directories.
    # This prevents redirecting the Gateway to another location.
    settings = get_gateway_settings()

    root = Path(settings.socket_root)

    if root.is_symlink() or socket_path.parent.is_symlink():
        raise ValueError(
            "Symbolic links are not allowed in IDE socket paths."
        )


def create_ide_http_client(sandbox_id: UUID) -> httpx.AsyncClient:
    """
    Create an HTTP client connected to a sandbox's Unix socket.

    The caller must close the client after use.
    """

    # STEP 1: Build the sandbox's socket path.
    socket_path = get_ide_socket_path(sandbox_id)

    # STEP 2: Verify the socket exists and is valid.
    validate_ide_socket(socket_path)

    # STEP 3: Create an HTTP transport using the Unix socket.
    transport = httpx.AsyncHTTPTransport(
        uds=os.fspath(socket_path),
        retries=0,
    )

    # STEP 4: Create an HTTP client for the IDE.
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://localhost",
        timeout=30.0,
        follow_redirects=False,
    )