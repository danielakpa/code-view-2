"""Manage one-time browser handoff tickets for CodeView IDEs."""

import hashlib
import secrets

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import UUID


# A handoff ticket is valid for only 30 seconds.
HANDOFF_LIFETIME_SECONDS = 30


@dataclass(frozen=True)
class IDEHandoff:
    """
    Represents a temporary authorization to open an IDE.

    The ticket itself is never stored in plain text.
    """

    sandbox_id: UUID
    expires_at: datetime


class IDEHandoffStore:
    """
    Create and consume one-time browser handoff tickets.

    This implementation uses memory for local development.
    """

    def __init__(self):

        # Store tickets using SHA-256 hashes.
        self._tickets: dict[str, IDEHandoff] = {}

        # Protect ticket operations from simultaneous threads.
        self._lock = Lock()

    def create_ticket(self, sandbox_id: UUID) -> str:
        """
        Generate a one-time browser handoff ticket.

        Returns the raw ticket for delivery to the browser.
        """

        # STEP 1: Validate the sandbox UUID.
        if not isinstance(sandbox_id, UUID) or sandbox_id.int == 0:
            raise ValueError("Invalid sandbox identity.")

        # STEP 2: Generate an unpredictable ticket.
        ticket = secrets.token_urlsafe(32)

        # STEP 3: Hash the ticket before storing it.
        ticket_hash = hashlib.sha256(
            ticket.encode("utf-8")
        ).hexdigest()

        # STEP 4: Set a short expiration time.
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=HANDOFF_LIFETIME_SECONDS
        )

        # STEP 5: Store the ticket.
        with self._lock:
            self._tickets[ticket_hash] = IDEHandoff(
                sandbox_id=sandbox_id,
                expires_at=expires_at,
            )

        # STEP 6: Return the one-time ticket.
        return ticket

    def consume_ticket(self, ticket: str) -> UUID | None:
        """
        Consume a handoff ticket exactly once.

        Returns the sandbox UUID when valid.
        Returns None when invalid, expired, or already used.
        """

        # STEP 1: Reject invalid input.
        if not isinstance(ticket, str) or not ticket:
            return None

        # STEP 2: Hash the supplied ticket.
        ticket_hash = hashlib.sha256(
            ticket.encode("utf-8")
        ).hexdigest()

        # STEP 3: Atomically remove the ticket.
        with self._lock:
            handoff = self._tickets.pop(
                ticket_hash,
                None,
            )

        # STEP 4: Reject missing tickets.
        if handoff is None:
            return None

        # STEP 5: Reject expired tickets.
        if handoff.expires_at <= datetime.now(timezone.utc):
            return None

        # STEP 6: Return the authorized sandbox.
        return handoff.sandbox_id


# Shared handoff store for the current Gateway process.
ide_handoff_store = IDEHandoffStore()