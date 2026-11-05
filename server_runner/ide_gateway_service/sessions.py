"""Manage temporary browser sessions for CodeView developer IDEs."""

import hashlib
import secrets

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import UUID


# Browser sessions expire after 30 minutes.
SESSION_LIFETIME_MINUTES = 30


@dataclass(frozen=True)
class IDESession:
    """
    Represents an authorized browser IDE session.

    Each session belongs to:
    - One developer.
    - One private development group.
    - One sandbox.

    The raw session token is never stored.
    """

    developer_id: int
    group_id: int
    sandbox_id: UUID
    created_at: datetime
    expires_at: datetime


class IDESessionStore:
    """
    Manage temporary IDE browser sessions.

    This implementation uses process-local memory.

    Production deployments with multiple Gateway workers
    will require shared storage for reliable revocation.
    """

    def __init__(self):

        # Store sessions using SHA-256 hashes of their tokens.
        self._sessions: dict[str, IDESession] = {}

        # Protect session operations from concurrent threads.
        self._lock = Lock()

    def create_session(
        self,
        developer_id: int,
        group_id: int,
        sandbox_id: UUID,
    ) -> str:
        """
        Create a browser session for a verified IDE identity.

        Returns a random token for a secure, HttpOnly cookie.
        """

        # STEP 1: Validate the developer identity.
        if type(developer_id) is not int or developer_id <= 0:
            raise ValueError("Invalid developer identity.")

        # STEP 2: Validate the group identity.
        if type(group_id) is not int or group_id <= 0:
            raise ValueError("Invalid group identity.")

        # STEP 3: Validate the sandbox identity.
        if not isinstance(sandbox_id, UUID) or sandbox_id.int == 0:
            raise ValueError("Invalid sandbox identity.")

        # STEP 4: Generate a cryptographically secure token.
        token = secrets.token_urlsafe(32)

        # STEP 5: Hash the token before storing it.
        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        # STEP 6: Calculate session expiration.
        now = datetime.now(timezone.utc)

        session = IDESession(
            developer_id=developer_id,
            group_id=group_id,
            sandbox_id=sandbox_id,
            created_at=now,
            expires_at=now + timedelta(
                minutes=SESSION_LIFETIME_MINUTES
            ),
        )

        # STEP 7: Store the session safely.
        with self._lock:
            self._sessions[token_hash] = session

        # STEP 8: Return the raw token to the caller.
        return token

    def get_session(self, token: str) -> IDESession | None:
        """
        Return a valid session or None.

        Expired sessions are removed when accessed.
        """

        # STEP 1: Reject invalid tokens.
        if not isinstance(token, str) or not token:
            return None

        # STEP 2: Hash the supplied token.
        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        # STEP 3: Look up the session.
        with self._lock:
            session = self._sessions.get(token_hash)

            if session is None:
                return None

            # STEP 4: Reject expired sessions.
            if session.expires_at <= datetime.now(timezone.utc):
                self._sessions.pop(token_hash, None)
                return None

            # STEP 5: Return the valid session.
            return session

    def revoke_session(self, token: str) -> bool:
        """
        Revoke one IDE browser session.

        Returns True if the session existed.
        """

        # STEP 1: Reject invalid tokens.
        if not isinstance(token, str) or not token:
            return False

        # STEP 2: Hash the token.
        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        # STEP 3: Remove the session safely.
        with self._lock:
            return self._sessions.pop(token_hash, None) is not None

    def revoke_developer_group_sessions(
        self,
        developer_id: int,
        group_id: int,
    ) -> int:
        """
        Revoke all IDE sessions for a developer in one group.

        Returns the number of sessions removed.
        """

        # STEP 1: Validate the developer identity.
        if type(developer_id) is not int or developer_id <= 0:
            raise ValueError("Invalid developer identity.")

        # STEP 2: Validate the group identity.
        if type(group_id) is not int or group_id <= 0:
            raise ValueError("Invalid group identity.")

        # STEP 3: Lock the store while removing sessions.
        with self._lock:

            # Find every session belonging to this developer
            # inside this particular group.
            matching_tokens = [
                token_hash
                for token_hash, session in self._sessions.items()
                if (
                    session.developer_id == developer_id
                    and session.group_id == group_id
                )
            ]

            # Remove all matching sessions.
            for token_hash in matching_tokens:
                self._sessions.pop(token_hash, None)

            # Return how many sessions were revoked.
            return len(matching_tokens)


# Shared session store within this Gateway process.
ide_session_store = IDESessionStore()