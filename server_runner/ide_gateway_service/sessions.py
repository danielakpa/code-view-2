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

    The raw session token is never stored.
    """

    sandbox_id: UUID
    created_at: datetime
    expires_at: datetime


class IDESessionStore:
    """
    Manage temporary IDE sessions.

    This initial implementation uses memory.
    Production deployments will require shared storage.
    """

    def __init__(self):

        # Store sessions using hashed tokens.
        self._sessions: dict[str, IDESession] = {}

        # Protect the dictionary from concurrent thread access.
        self._lock = Lock()

    def create_session(self, sandbox_id: UUID) -> str:
        """
        Create a browser session for an authorized sandbox.

        Returns a random token that can be placed in
        a secure, HttpOnly browser cookie.
        """

        # STEP 1: Validate the sandbox identity.
        if not isinstance(sandbox_id, UUID) or sandbox_id.int == 0:
            raise ValueError("Invalid sandbox identity.")

        # STEP 2: Generate a cryptographically secure token.
        token = secrets.token_urlsafe(32)

        # STEP 3: Hash the token before storing it.
        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        # STEP 4: Calculate session expiration.
        now = datetime.now(timezone.utc)

        session = IDESession(
            sandbox_id=sandbox_id,
            created_at=now,
            expires_at=now + timedelta(
                minutes=SESSION_LIFETIME_MINUTES
            ),
        )

        # STEP 5: Store the session safely.
        with self._lock:
            self._sessions[token_hash] = session

        # STEP 6: Return the raw token to the caller.
        return token

    def get_session(self, token: str) -> IDESession | None:
        """
        Return a valid session or None.

        Expired sessions are removed automatically
        when they are accessed.
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
        Revoke an IDE session.

        Returns True if the session existed.
        """

        if not isinstance(token, str) or not token:
            return False

        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        with self._lock:
            return self._sessions.pop(token_hash, None) is not None


# Shared session store for this Gateway process.
ide_session_store = IDESessionStore()