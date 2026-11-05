"""Tests for CodeView IDE browser sessions."""

import hashlib
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
        """Create a fresh session store and test identities."""

        # Create an independent session store for each test.
        self.store = IDESessionStore()

        # Define the developer and group identities.
        self.developer_id = 12
        self.group_id = 5

        # Generate a unique sandbox UUID.
        self.sandbox_id = uuid4()

        # Generate a membership generation for this test.
        self.membership_generation = uuid4()

    def create_test_session(
        self,
        developer_id=None,
        group_id=None,
        sandbox_id=None,
        membership_generation=None,
    ):
        """Create a session using the default test identities."""

        return self.store.create_session(
            developer_id=(
                self.developer_id
                if developer_id is None
                else developer_id
            ),
            group_id=(
                self.group_id
                if group_id is None
                else group_id
            ),
            sandbox_id=(
                self.sandbox_id
                if sandbox_id is None
                else sandbox_id
            ),
            membership_generation=(
                self.membership_generation
                if membership_generation is None
                else membership_generation
            ),
        )

    def test_create_session(self):
        """A valid identity should receive a session token."""

        token = self.create_test_session()

        self.assertIsInstance(token, str)
        self.assertTrue(token)

    def test_get_valid_session(self):
        """A valid session must preserve all four identities."""

        token = self.create_test_session()

        session = self.store.get_session(token)

        self.assertIsNotNone(session)

        # Verify the developer identity.
        self.assertEqual(
            session.developer_id,
            self.developer_id,
        )

        # Verify the group identity.
        self.assertEqual(
            session.group_id,
            self.group_id,
        )

        # Verify the sandbox identity.
        self.assertEqual(
            session.sandbox_id,
            self.sandbox_id,
        )

        # Verify the membership generation.
        self.assertEqual(
            session.membership_generation,
            self.membership_generation,
        )

    def test_session_preserves_membership_generation(self):
        """A session must preserve its membership generation."""

        # Create a browser session.
        token = self.create_test_session()

        # Retrieve the stored session.
        session = self.store.get_session(token)

        # Confirm the session exists.
        self.assertIsNotNone(session)

        # Confirm the correct generation was stored.
        self.assertEqual(
            session.membership_generation,
            self.membership_generation,
        )

    def test_invalid_token_rejected(self):
        """An unknown token must not authorize access."""

        session = self.store.get_session("invalid-token")

        self.assertIsNone(session)

    def test_expired_session_rejected(self):
        """Expired sessions must no longer authorize access."""

        token = self.create_test_session()

        # Find the stored session using its token hash.
        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        original = self.store.get_session(token)

        # Replace the session with an expired version.
        self.store._sessions[token_hash] = IDESession(
            developer_id=original.developer_id,
            group_id=original.group_id,
            sandbox_id=original.sandbox_id,
            membership_generation=original.membership_generation,
            created_at=original.created_at,
            expires_at=(
                datetime.now(timezone.utc)
                - timedelta(seconds=1)
            ),
        )

        # The expired session must be rejected.
        self.assertIsNone(self.store.get_session(token))

    def test_revoke_session(self):
        """Revoking a session must invalidate its token."""

        token = self.create_test_session()

        revoked = self.store.revoke_session(token)

        self.assertTrue(revoked)
        self.assertIsNone(self.store.get_session(token))

    def test_invalid_sandbox_rejected(self):
        """An empty sandbox UUID must be rejected."""

        with self.assertRaises(ValueError):
            self.create_test_session(
                sandbox_id=UUID(int=0)
            )

    def test_invalid_membership_generation_rejected(self):
        """An empty membership generation must be rejected."""

        with self.assertRaises(ValueError):
            self.create_test_session(
                membership_generation=UUID(int=0)
            )

    def test_invalid_membership_generation_type_rejected(self):
        """A membership generation must be a UUID object."""

        with self.assertRaises(ValueError):
            self.create_test_session(
                membership_generation="invalid-generation"
            )

    def test_sessions_have_unique_tokens(self):
        """Each session must receive a different token."""

        first = self.create_test_session()
        second = self.create_test_session()

        self.assertNotEqual(first, second)

    def test_invalid_developer_rejected(self):
        """Developer IDs must be positive integers."""

        with self.assertRaises(ValueError):
            self.create_test_session(
                developer_id=0
            )

        # Boolean values must not be accepted as IDs.
        with self.assertRaises(ValueError):
            self.create_test_session(
                developer_id=True
            )

    def test_invalid_group_rejected(self):
        """Group IDs must be positive integers."""

        with self.assertRaises(ValueError):
            self.create_test_session(
                group_id=0
            )

        # Boolean values must not be accepted as IDs.
        with self.assertRaises(ValueError):
            self.create_test_session(
                group_id=True
            )

    def test_revoke_developer_group_sessions(self):
        """Revoke all sessions for one developer in one group."""

        # Developer 12 opens two sessions in Group 5.
        first = self.create_test_session()
        second = self.create_test_session()

        # Developer 12 also has a session in Group 8.
        other_group = self.create_test_session(
            group_id=8,
            sandbox_id=uuid4(),
        )

        # Revoke Developer 12's access to Group 5.
        removed = self.store.revoke_developer_group_sessions(
            developer_id=12,
            group_id=5,
        )

        # Both Group 5 sessions must be removed.
        self.assertEqual(removed, 2)
        self.assertIsNone(self.store.get_session(first))
        self.assertIsNone(self.store.get_session(second))

        # The Group 8 session must remain valid.
        self.assertIsNotNone(
            self.store.get_session(other_group)
        )

    def test_revocation_does_not_affect_other_developers(self):
        """Revoking one developer must not affect another."""

        # Developer 12 opens an IDE in Group 5.
        first = self.create_test_session()

        # Developer 20 opens an IDE in the same group.
        second = self.create_test_session(
            developer_id=20,
            sandbox_id=uuid4(),
        )

        # Revoke Developer 12's sessions.
        removed = self.store.revoke_developer_group_sessions(
            developer_id=12,
            group_id=5,
        )

        # Only Developer 12 should lose access.
        self.assertEqual(removed, 1)
        self.assertIsNone(self.store.get_session(first))
        self.assertIsNotNone(self.store.get_session(second))


if __name__ == "__main__":
    unittest.main()