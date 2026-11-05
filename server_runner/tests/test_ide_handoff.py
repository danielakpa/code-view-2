"""Tests for CodeView IDE browser handoff tickets."""

import hashlib
import unittest

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from server_runner.ide_gateway_service.handoff import (
    IDEHandoff,
    IDEHandoffStore,
)

from server_runner.ide_gateway_service.runner_client import (
    VerifiedIDEIdentity,
)


class TestIDEHandoff(unittest.TestCase):
    """Test one-time IDE handoff ticket management."""

    def setUp(self):
        """Prepare a fresh ticket store for each test."""

        # STEP 1: Create an independent ticket store.
        self.store = IDEHandoffStore()

        # STEP 2: Generate test identities.
        self.developer_id = 12
        self.group_id = 5
        self.sandbox_id = uuid4()

        # STEP 3: Create the identity verified by the Runner.
        self.identity = VerifiedIDEIdentity(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=self.sandbox_id,
        )

    def test_create_ticket(self):
        """A verified IDE identity should receive a handoff ticket."""

        # Create a one-time ticket.
        ticket = self.store.create_ticket(self.identity)

        # Verify that the ticket was created.
        self.assertIsInstance(ticket, str)
        self.assertTrue(ticket)

    def test_valid_ticket(self):
        """A valid ticket must preserve all verified identities."""

        # Create a ticket using the verified identity.
        ticket = self.store.create_ticket(self.identity)

        # Consume the ticket.
        result = self.store.consume_ticket(ticket)

        # Verify the returned handoff object.
        self.assertIsInstance(result, IDEHandoff)

        # Verify the developer identity.
        self.assertEqual(
            result.developer_id,
            self.developer_id,
        )

        # Verify the group identity.
        self.assertEqual(
            result.group_id,
            self.group_id,
        )

        # Verify the sandbox identity.
        self.assertEqual(
            result.sandbox_id,
            self.sandbox_id,
        )

    def test_ticket_cannot_be_reused(self):
        """A handoff ticket must work only once."""

        # Create a valid ticket.
        ticket = self.store.create_ticket(self.identity)

        # Attempt to consume the same ticket twice.
        first_result = self.store.consume_ticket(ticket)
        second_result = self.store.consume_ticket(ticket)

        # The first attempt must succeed.
        self.assertIsInstance(first_result, IDEHandoff)
        self.assertEqual(
            first_result.sandbox_id,
            self.sandbox_id,
        )

        # The second attempt must fail.
        self.assertIsNone(second_result)

    def test_expired_ticket_rejected(self):
        """Expired tickets must not authorize access."""

        # Create a valid ticket.
        ticket = self.store.create_ticket(self.identity)

        # Find the stored ticket using its SHA-256 hash.
        ticket_hash = hashlib.sha256(
            ticket.encode("utf-8")
        ).hexdigest()

        # Replace the stored ticket with an expired version.
        self.store._tickets[ticket_hash] = IDEHandoff(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=self.sandbox_id,
            expires_at=(
                datetime.now(timezone.utc)
                - timedelta(seconds=1)
            ),
        )

        # Attempt to consume the expired ticket.
        result = self.store.consume_ticket(ticket)

        # Expired tickets must be rejected.
        self.assertIsNone(result)

    def test_invalid_ticket_rejected(self):
        """Unknown tickets must not authorize access."""

        # Attempt to consume a nonexistent ticket.
        result = self.store.consume_ticket("invalid-ticket")

        # Unknown tickets must be rejected.
        self.assertIsNone(result)

    def test_invalid_sandbox_rejected(self):
        """Reject an empty sandbox UUID."""

        # Create an identity with an invalid sandbox UUID.
        invalid_identity = VerifiedIDEIdentity(
            developer_id=self.developer_id,
            group_id=self.group_id,
            sandbox_id=UUID(int=0),
        )

        # The ticket store must reject the invalid identity.
        with self.assertRaises(ValueError):
            self.store.create_ticket(invalid_identity)

    def test_tickets_are_unique(self):
        """Every ticket must have a different random value."""

        # Create two tickets for the same IDE.
        first = self.store.create_ticket(self.identity)
        second = self.store.create_ticket(self.identity)

        # Each ticket must be unique.
        self.assertNotEqual(first, second)

    def test_invalid_developer_rejected(self):
        """Reject invalid developer identities."""

        # Developer IDs must be positive integers.
        invalid_identity = VerifiedIDEIdentity(
            developer_id=0,
            group_id=self.group_id,
            sandbox_id=self.sandbox_id,
        )

        with self.assertRaises(ValueError):
            self.store.create_ticket(invalid_identity)

    def test_invalid_group_rejected(self):
        """Reject invalid group identities."""

        # Group IDs must be positive integers.
        invalid_identity = VerifiedIDEIdentity(
            developer_id=self.developer_id,
            group_id=0,
            sandbox_id=self.sandbox_id,
        )

        with self.assertRaises(ValueError):
            self.store.create_ticket(invalid_identity)

    def test_handoff_identity_is_immutable(self):
        """Prevent ordinary modification of stored handoff identities."""

        # Create and consume a valid ticket.
        ticket = self.store.create_ticket(self.identity)
        handoff = self.store.consume_ticket(ticket)

        # A frozen dataclass must reject field reassignment.
        with self.assertRaises(FrozenInstanceError):
            handoff.developer_id = 999


if __name__ == "__main__":
    unittest.main()