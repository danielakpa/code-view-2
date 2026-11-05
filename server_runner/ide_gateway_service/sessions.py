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
    - One membership generation.

    The raw session token is never stored.
    """

    # Identify the developer who owns the IDE.
    developer_id: int

    # Identify the private development group.
    group_id: int

    # Identify the developer's Docker sandbox.
    sandbox_id: UUID

    # Identify the membership used to authorize this session.
    membership_generation: UUID

    # Record when the session was created.
    created_at: datetime

    # Record when the session expires.
    expires_at: datetime


class IDESessionStore:
    """
    Manage temporary IDE browser sessions.

    This implementation uses process-local memory.

    Production deployments with multiple Gateway workers
    will require shared storage for reliable revocation.

    IMPORTANT:
    Storing membership_generation does not automatically
    verify whether the membership is still active.
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
        membership_generation: UUID,
    ) -> str:
        """
        Create a browser session for a verified IDE identity.

        Returns a random token for a secure, HttpOnly cookie.

        The caller must supply the membership generation
        obtained from the verified IDE handoff.
        """

        # =====================================================
        # STEP 1: VALIDATE THE DEVELOPER IDENTITY
        # =====================================================

        if type(developer_id) is not int or developer_id <= 0:
            raise ValueError("Invalid developer identity.")

        # =====================================================
        # STEP 2: VALIDATE THE GROUP IDENTITY
        # =====================================================

        if type(group_id) is not int or group_id <= 0:
            raise ValueError("Invalid group identity.")

        # =====================================================
        # STEP 3: VALIDATE THE SANDBOX IDENTITY
        # =====================================================

        if (
            not isinstance(sandbox_id, UUID)
            or sandbox_id.int == 0
        ):
            raise ValueError("Invalid sandbox identity.")

        # =====================================================
        # STEP 4: VALIDATE THE MEMBERSHIP GENERATION
        # =====================================================

        # Reject missing, incorrectly typed, or empty generations.
        if (
            not isinstance(membership_generation, UUID)
            or membership_generation.int == 0
        ):
            raise ValueError(
                "Invalid IDE membership generation."
            )

        # =====================================================
        # STEP 5: GENERATE A SECURE SESSION TOKEN
        # =====================================================

        # Generate a cryptographically secure random token.
        token = secrets.token_urlsafe(32)

        # =====================================================
        # STEP 6: HASH THE SESSION TOKEN
        # =====================================================

        # Never store the raw browser session token.
        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        # =====================================================
        # STEP 7: CALCULATE SESSION EXPIRATION
        # =====================================================

        now = datetime.now(timezone.utc)

        # Create an immutable browser session.
        session = IDESession(
            developer_id=developer_id,
            group_id=group_id,
            sandbox_id=sandbox_id,
            membership_generation=membership_generation,
            created_at=now,
            expires_at=now + timedelta(
                minutes=SESSION_LIFETIME_MINUTES
            ),
        )

        # =====================================================
        # STEP 8: STORE THE SESSION SAFELY
        # =====================================================

        # Protect the shared dictionary from concurrent writes.
        with self._lock:
            self._sessions[token_hash] = session

        # =====================================================
        # STEP 9: RETURN THE SESSION TOKEN
        # =====================================================

        # The Gateway will place this token in a secure cookie.
        return token

    def get_session(self, token: str) -> IDESession | None:
        """
        Return a valid session or None.

        Expired sessions are removed when accessed.

        NOTE:
        This currently checks token validity and expiration,
        not whether the developer's membership remains active.
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