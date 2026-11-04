"""Tests for CodeView IDE Unix socket connections."""

import os
import socket
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from server_runner.ide_gateway_service.socket_connection import (
    get_ide_socket_path,
    validate_ide_socket,
    create_ide_http_client,
)


class TestIDESocketConnection(unittest.TestCase):
    """Test IDE socket paths, validation, and HTTP clients."""

    def setUp(self):
        """Create an isolated socket directory for each test."""

        # Create a temporary directory.
        self.temp_directory = tempfile.TemporaryDirectory()

        # Store its path.
        self.socket_root = Path(self.temp_directory.name)

        # Create a test sandbox identity.
        self.sandbox_id = uuid4()

        # Configure the Gateway to use our temporary directory.
        self.environment = patch.dict(
            os.environ,
            {
                "CODEVIEW_IDE_SOCKET_ROOT": str(self.socket_root),
                "CODEVIEW_RUNNER_INTERNAL_URL": "http://127.0.0.1:8001",
                "CODEVIEW_RUNNER_TOKEN": "test-runner-token",
            },
        )

        self.environment.start()

    def tearDown(self):
        """Restore the environment and remove temporary files."""

        self.environment.stop()
        self.temp_directory.cleanup()

    def create_test_socket(self):
        """Create a real Unix socket inside the test directory."""

        # Get the expected socket path.
        socket_path = get_ide_socket_path(self.sandbox_id)

        # Create the sandbox directory.
        socket_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # Create a Unix socket.
        test_socket = socket.socket(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        )

        # Bind the socket to its filesystem path.
        test_socket.bind(str(socket_path))

        # Return both objects for testing.
        return test_socket, socket_path

    def test_socket_path_uses_sandbox_id(self):
        """The socket path must belong to the correct sandbox."""

        socket_path = get_ide_socket_path(self.sandbox_id)

        expected_path = (
            self.socket_root
            / f"codeview-{self.sandbox_id.hex}"
            / "ide.sock"
        )

        self.assertEqual(socket_path, expected_path)

    def test_invalid_sandbox_id_rejected(self):
        """An empty sandbox UUID must be rejected."""

        with self.assertRaises(ValueError):
            get_ide_socket_path(UUID(int=0))

    def test_missing_socket_rejected(self):
        """A nonexistent socket must be rejected."""

        socket_path = get_ide_socket_path(self.sandbox_id)

        with self.assertRaises(FileNotFoundError):
            validate_ide_socket(socket_path)

    def test_regular_file_rejected(self):
        """An ordinary file must not be accepted as an IDE socket."""

        socket_path = get_ide_socket_path(self.sandbox_id)

        # Create the parent directory.
        socket_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # Create an ordinary file instead of a socket.
        socket_path.touch()

        with self.assertRaises(ValueError):
            validate_ide_socket(socket_path)

    def test_valid_unix_socket_accepted(self):
        """A real Unix socket should pass validation."""

        test_socket, socket_path = self.create_test_socket()

        try:
            # This should complete without raising an exception.
            validate_ide_socket(socket_path)

            self.assertTrue(socket_path.exists())

        finally:
            test_socket.close()

    def test_symbolic_link_socket_rejected(self):
        """A symbolic link must not be accepted as an IDE socket."""

        test_socket, real_socket_path = self.create_test_socket()

        try:
            # Create a symbolic link pointing to the real socket.
            linked_socket_path = (
                real_socket_path.parent / "linked.sock"
            )

            linked_socket_path.symlink_to(real_socket_path)

            # The symbolic link is not itself a Unix socket.
            with self.assertRaises(ValueError):
                validate_ide_socket(linked_socket_path)

        finally:
            test_socket.close()

    def test_http_client_creation(self):
        """A valid socket should allow HTTP client creation."""

        test_socket, socket_path = self.create_test_socket()

        try:
            # Create the HTTP client.
            client = create_ide_http_client(self.sandbox_id)

            # Confirm that the client uses an HTTPX transport.
            self.assertIsNotNone(client)

            # Close the client without starting an event loop.
            import asyncio

            asyncio.run(client.aclose())

        finally:
            test_socket.close()


if __name__ == "__main__":
    unittest.main()