"""Tests for CodeView's protected browser IDE route."""

import hashlib
import unittest

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from server_runner.ide_gateway_service.main import app

from server_runner.ide_gateway_service.sessions import (
    IDESession,
    ide_session_store,
)


class TestIDEProtectedRoute(unittest.TestCase):
    """Verify that only valid IDE sessions can access the route."""

    def setUp(self):
        """Prepare a fresh test environment."""

        # Create the FastAPI test client.
        self.client = TestClient(app)

        # Clear previous test sessions.
        with ide_session_store._lock:
            ide_session_store._sessions.clear()

        # Create a unique sandbox for testing.
        self.sandbox_id = uuid4()

    def tearDown(self):
        """Clean up after each test."""

        self.client.close()

        # Remove test sessions.
        with ide_session_store._lock:
            ide_session_store._sessions.clear()

    def test_missing_session_rejected(self):
        """A browser without a session must be rejected."""

        response = self.client.get("/ide/")

        self.assertEqual(response.status_code, 401)

    def test_valid_session_accepted(self):
        """A valid session should authorize IDE access."""

        # Create a valid session.
        token = ide_session_store.create_session(
            self.sandbox_id
        )

        # Send the session cookie to the Gateway.
        response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": token,
            },
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["authorized"])

        self.assertEqual(
            data["sandbox_id"],
            str(self.sandbox_id),
        )

    def test_invalid_session_rejected(self):
        """An unknown session token must be rejected."""

        response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": "invalid-token",
            },
        )

        self.assertEqual(response.status_code, 401)

    def test_expired_session_rejected(self):
        """An expired session must not authorize access."""

        # Create a valid session.
        token = ide_session_store.create_session(
            self.sandbox_id
        )

        # Find its stored hash.
        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        # Read the existing session.
        session = ide_session_store.get_session(token)

        # Replace its expiration with a past time.
        with ide_session_store._lock:
            ide_session_store._sessions[token_hash] = IDESession(
                sandbox_id=session.sandbox_id,
                created_at=session.created_at,
                expires_at=(
                    datetime.now(timezone.utc)
                    - timedelta(seconds=1)
                ),
            )

        # Attempt access using the expired session.
        response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": token,
            },
        )

        self.assertEqual(response.status_code, 401)

    def test_revoked_session_rejected(self):
        """A revoked session must no longer work."""

        # Create a valid session.
        token = ide_session_store.create_session(
            self.sandbox_id
        )

        # Revoke the session.
        ide_session_store.revoke_session(token)

        # Attempt access with the revoked token.
        response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": token,
            },
        )

        self.assertEqual(response.status_code, 401)

    def test_session_belongs_to_correct_sandbox(self):
        """The Gateway must return the session's sandbox."""

        first_sandbox = uuid4()
        second_sandbox = uuid4()

        # Create independent sessions.
        first_token = ide_session_store.create_session(
            first_sandbox
        )

        second_token = ide_session_store.create_session(
            second_sandbox
        )

        # Access the IDE with the first session.
        first_response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": first_token,
            },
        )

        # Access the IDE with the second session.
        second_response = self.client.get(
            "/ide/",
            cookies={
                "codeview_ide_session": second_token,
            },
        )

        # Each session must identify its own sandbox.
        self.assertEqual(
            first_response.json()["sandbox_id"],
            str(first_sandbox),
        )

        self.assertEqual(
            second_response.json()["sandbox_id"],
            str(second_sandbox),
        )


if __name__ == "__main__":
    unittest.main()