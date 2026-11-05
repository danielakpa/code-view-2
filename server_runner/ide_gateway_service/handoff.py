"""Manage one-time browser handoff tickets for CodeView IDEs."""

import hashlib
import secrets

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import UUID

from server_runner.ide_gateway_service.runner_client import (
    VerifiedIDEIdentity,
)


# A handoff ticket is valid for only 30 seconds.
HANDOFF_LIFETIME_SECONDS = 30


@dataclass(frozen=True)
class IDEHandoff:
    """
    Store the verified developer, group, sandbox, and membership identities.

    The raw handoff ticket is never stored in plain text.
    """

    # Identify the developer who owns the IDE.
    developer_id: int

    # Identify the private development group.
    group_id: int

    # Identify the developer's Docker sandbox.
    sandbox_id: UUID

    # Identify the membership used to authorize this IDE.
    membership_generation: UUID

    # Identify when the handoff ticket expires.
    expires_at: datetime


class IDEHandoffStore:
    """
    Create and consume one-time browser handoff tickets.

    This implementation uses memory for local development.
    Production deployments with multiple Gateway processes
    will require a shared, atomic ticket store.
    """

    def __init__(self):

        # Store tickets using SHA-256 hashes.
        self._tickets: dict[str, IDEHandoff] = {}

        # Protect ticket operations from simultaneous threads.
        self._lock = Lock()

    def create_ticket(
        self,
        identity: VerifiedIDEIdentity,
    ) -> str:
        """
        Generate a one-time browser handoff ticket.

        The identity must come from the Runner's verified response.
        """

        # STEP 1: Validate the verified identity object.
        if not isinstance(identity, VerifiedIDEIdentity):
            raise ValueError("Invalid IDE identity.")

        # STEP 2: Validate developer and group IDs.
        if (
            type(identity.developer_id) is not int
            or identity.developer_id <= 0
            or type(identity.group_id) is not int
            or identity.group_id <= 0
        ):
            raise ValueError("Invalid developer or group identity.")

        # STEP 3: Validate the sandbox UUID.
        if (
            not isinstance(identity.sandbox_id, UUID)
            or identity.sandbox_id.int == 0
        ):
            raise ValueError("Invalid sandbox identity.")

        # STEP 4: Validate the membership generation.
        #
        # An empty or invalid generation must never be
        # accepted when creating a browser handoff ticket.
        if (
            not isinstance(identity.membership_generation, UUID)
            or identity.membership_generation.int == 0
        ):
            raise ValueError(
                "Invalid IDE membership generation."
            )

        # STEP 5: Generate an unpredictable ticket.
        ticket = secrets.token_urlsafe(32)

        # STEP 6: Hash the ticket before storing it.
        #
        # Only the hash is stored in memory.
        ticket_hash = hashlib.sha256(
            ticket.encode("utf-8")
        ).hexdigest()

        # STEP 7: Set a short expiration time.
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=HANDOFF_LIFETIME_SECONDS
        )

        # STEP 8: Store all verified identities.
        with self._lock:
            self._tickets[ticket_hash] = IDEHandoff(
                developer_id=identity.developer_id,
                group_id=identity.group_id,
                sandbox_id=identity.sandbox_id,
                membership_generation=identity.membership_generation,
                expires_at=expires_at,
            )

        # STEP 9: Return the one-time ticket.
        return ticket

    def consume_ticket(
        self,
        ticket: str,
    ) -> IDEHandoff | None:
        """
        Consume a handoff ticket exactly once.

        Return the verified handoff information when valid.
        Return None when invalid, expired, or already used.
        """

        # STEP 1: Reject invalid input.
        if not isinstance(ticket, str) or not ticket:
            return None

        # STEP 2: Hash the supplied ticket.
        ticket_hash = hashlib.sha256(
            ticket.encode("utf-8")
        ).hexdigest()

        # STEP 3: Atomically remove the ticket.
        #
        # The lock prevents two threads from consuming
        # the same ticket simultaneously.
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

        # STEP 6: Return the verified handoff information.
        return handoff


# Shared handoff store for the current Gateway process.
ide_handoff_store = IDEHandoffStore()