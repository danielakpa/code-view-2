"""Automatically renew PostgreSQL leases for IDE operations."""

import logging
import threading

from uuid import UUID

from server_runner.sandbox_controller.database import (
    create_controller_session_factory,
)

from server_runner.sandbox_controller.database_replay import (
    DatabaseReplayStore,
)


logger = logging.getLogger(__name__)


class IDELeaseHeartbeat:
    """Keep an IDE operation's PostgreSQL lease active."""

    # Renew every 30 seconds.
    RENEW_INTERVAL_SECONDS = 30

    def __init__(
        self,
        request_id: UUID,
        lease_owner: UUID,
        lease_generation: int,
    ):
        """Prepare the lease heartbeat."""

        # Identify the operation and its owner.
        self.request_id = request_id
        self.lease_owner = lease_owner
        self.lease_generation = lease_generation

        # Signal the background worker to stop.
        self._stop_event = threading.Event()

        # Track whether ownership was lost.
        self._lost_lease = threading.Event()

        # Store the background thread.
        self._thread = None

    @property
    def lost_lease(self):
        """Check whether lease renewal failed."""
        return self._lost_lease.is_set()

    def _renew(self):
        """Renew the lease using a separate PostgreSQL session."""

        # Get the existing session factory.
        session_factory = create_controller_session_factory()

        # Create a session owned by this heartbeat thread.
        with session_factory() as db:

            replay_store = DatabaseReplayStore(db)

            try:
                # Extend the lease only if ownership is valid.
                renewed = replay_store.renew_lease(
                    request_id=self.request_id,
                    lease_owner=self.lease_owner,
                    lease_generation=self.lease_generation,
                )

                if not renewed:
                    db.rollback()
                    return False

                # Save the new expiration time.
                db.commit()

                return True

            except Exception:
                db.rollback()
                raise

    def _run(self):
        """Periodically renew the lease until stopped."""

        while not self._stop_event.wait(
            self.RENEW_INTERVAL_SECONDS
        ):
            try:
                # Attempt to extend the lease.
                if not self._renew():
                    self._lost_lease.set()

                    logger.error(
                        "IDE lease ownership lost: %s",
                        self.request_id,
                    )

                    return

            except Exception:
                # Database failures must not be ignored.
                self._lost_lease.set()

                logger.exception(
                    "IDE lease renewal failed: %s",
                    self.request_id,
                )

                return

    def start(self):
        """Start the heartbeat in a background thread."""

        if self._thread is not None:
            raise RuntimeError(
                "IDE lease heartbeat has already started."
            )

        self._thread = threading.Thread(
            target=self._run,
            name=f"ide-lease-{self.request_id}",
            daemon=True,
        )

        self._thread.start()

    def stop(self):
        """Stop the heartbeat and wait for it to finish."""

        self._stop_event.set()

        if self._thread is not None:
            self._thread.join()