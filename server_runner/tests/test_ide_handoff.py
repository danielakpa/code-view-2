"""Tests for CodeView IDE browser handoff tickets."""

import hashlib
import unittest

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from server_runner.ide_gateway_service.handoff import (
    IDEHandoff,
    IDEHandoffStore,
)


class TestIDEHandoff(unittest.TestCase):
    """Test one-time IDE handoff ticket management."""

    def setUp(self):
        """Prepare a fresh ticket store for each test."""

        # Create an independent ticket store.
        self.store = IDEHandoffStore()

        # Generate a test sandbox identity.
        self.sandbox_id = uuid4()

    def test_create_ticket(self):
        """A valid sandbox should receive a handoff ticket."""

        ticket = self.store.create_ticket(self.sandbox_id)

        self.assertIsInstance(ticket, str)
        self.assertTrue(ticket)

    def test_valid_ticket(self):
        """A valid ticket should return the correct sandbox."""

        ticket = self.store.create_ticket(self.sandbox_id)

        result = self.store.consume_ticket(ticket)

        self.assertEqual(result, self.sandbox_id)

    def test_ticket_cannot_be_reused(self):
        """A handoff ticket must work only once."""

        ticket = self.store.create_ticket(self.sandbox_id)

        first_result = self.store.consume_ticket(ticket)
        second_result = self.store.consume_ticket(ticket)

        self.assertEqual(first_result, self.sandbox_id)
        self.assertIsNone(second_result)

    def test_expired_ticket_rejected(self):
        """Expired tickets must not authorize access."""

        ticket = self.store.create_ticket(self.sandbox_id)

        # Find the stored ticket using its hash.
        ticket_hash = hashlib.sha256(
            ticket.encode("utf-8")
        ).hexdigest()

        # Simulate an expired ticket.
        self.store._tickets[ticket_hash] = IDEHandoff(
            sandbox_id=self.sandbox_id,
            expires_at=datetime.now(timezone.utc)
            - timedelta(seconds=1),
        )

        result = self.store.consume_ticket(ticket)

        self.assertIsNone(result)

    def test_invalid_ticket_rejected(self):
        """Unknown tickets must not authorize access."""

        result = self.store.consume_ticket("invalid-ticket")

        self.assertIsNone(result)

    def test_invalid_sandbox_rejected(self):
        """Reject an empty sandbox UUID."""

        with self.assertRaises(ValueError):
            self.store.create_ticket(UUID(int=0))

    def test_tickets_are_unique(self):
        """Every ticket must have a different random value."""

        first = self.store.create_ticket(self.sandbox_id)
        second = self.store.create_ticket(self.sandbox_id)

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()