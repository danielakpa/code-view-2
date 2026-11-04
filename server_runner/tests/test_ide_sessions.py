"""Tests for CodeView IDE browser sessions."""

import unittest

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from server_runner.ide_gateway_service.sessions import (
    IDESession,
    IDESessionStore,
)


class TestIDESessions(unittest.TestCase):
    """Test session creation, validation, and revocation."""

    def setUp(self):
        # Create a fresh session store for every test.
        self.store = IDESessionStore()

        # Generate a test sandbox ID.
        self.sandbox_id = uuid4()

    def test_create_session(self):
        """A valid sandbox should receive a session token."""

        token = self.store.create_session(self.sandbox_id)

        self.assertIsInstance(token, str)
        self.assertTrue(token)

    def test_get_valid_session(self):
        """A valid token should return the correct sandbox."""

        token = self.store.create_session(self.sandbox_id)

        session = self.store.get_session(token)

        self.assertIsNotNone(session)
        self.assertEqual(session.sandbox_id, self.sandbox_id)

    def test_invalid_token_rejected(self):
        """An unknown token should not authorize access."""

        session = self.store.get_session("invalid-token")

        self.assertIsNone(session)

    def test_expired_session_rejected(self):
        """Expired sessions should no longer authorize access."""

        token = self.store.create_session(self.sandbox_id)

        # Find the stored session without exposing the raw token.
        import hashlib

        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        original = self.store.get_session(token)

        # Replace the session with an expired one.
        self.store._sessions[token_hash] = IDESession(
            sandbox_id=original.sandbox_id,
            created_at=original.created_at,
            expires_at=datetime.now(timezone.utc)
            - timedelta(seconds=1),
        )

        self.assertIsNone(self.store.get_session(token))

    def test_revoke_session(self):
        """Revoked sessions should immediately become invalid."""

        token = self.store.create_session(self.sandbox_id)

        revoked = self.store.revoke_session(token)

        self.assertTrue(revoked)
        self.assertIsNone(self.store.get_session(token))

    def test_invalid_sandbox_rejected(self):
        """An invalid sandbox UUID must not create a session."""

        with self.assertRaises(ValueError):
            self.store.create_session(UUID(int=0))

    def test_sessions_have_unique_tokens(self):
        """Each new session should receive a unique token."""

        first = self.store.create_session(self.sandbox_id)
        second = self.store.create_session(self.sandbox_id)

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()