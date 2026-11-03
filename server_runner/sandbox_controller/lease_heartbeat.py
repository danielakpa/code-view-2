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

    # Maximum time to wait for the heartbeat thread to stop.
    STOP_TIMEOUT_SECONDS = 10

    def __init__(
        self,
        request_id: UUID,
        lease_owner: UUID,
        lease_generation: int,
    ):
        """Prepare the lease heartbeat."""

        self.request_id = request_id
        self.lease_owner = lease_owner
        self.lease_generation = lease_generation

        # Tell the heartbeat when to stop.
        self._stop_event = threading.Event()

        # Record whether the lease was lost.
        self._lost_lease = threading.Event()

        # Background thread responsible for renewal.
        self._thread = None

    @property
    def lost_lease(self):
        """Return True if lease ownership was lost."""

        return self._lost_lease.is_set()

    def _renew(self):
        """Renew the lease using a separate PostgreSQL session."""

        session_factory = create_controller_session_factory()

        with session_factory() as db:

            replay_store = DatabaseReplayStore(db)

            try:
                # Renew only if this worker still owns the lease.
                renewed = replay_store.renew_lease(
                    request_id=self.request_id,
                    lease_owner=self.lease_owner,
                    lease_generation=self.lease_generation,
                )

                if not renewed:
                    db.rollback()
                    return False

                # Save the new lease expiration.
                db.commit()

                return True

            except Exception:
                db.rollback()
                raise

    def stop_and_renew(self):
        """
        Stop the heartbeat and renew the lease one final time.

        Returns True only when:
        1. The heartbeat thread has stopped.
        2. No heartbeat failure was detected.
        3. The worker successfully renewed its lease.
        """

        # Stop the background heartbeat first.
        self.stop()

        # Never renew while another heartbeat thread
        # might still be using the database.
        if self.lost_lease:
            return False

        try:
            # Give finalization a fresh lease.
            renewed = self._renew()

            if not renewed:
                self._lost_lease.set()
                return False

            return True

        except Exception:
            # Database errors make the handoff unsafe.
            self._lost_lease.set()

            logger.exception(
                "IDE lease handoff failed: %s",
                self.request_id,
            )

            return False                

    def _run(self):
        """Keep renewing until stopped or ownership is lost."""

        while not self._stop_event.wait(
            self.RENEW_INTERVAL_SECONDS
        ):

            try:
                renewed = self._renew()

                if not renewed:

                    self._lost_lease.set()

                    logger.error(
                        "IDE lease ownership lost: %s",
                        self.request_id,
                    )

                    return

            except Exception:

                self._lost_lease.set()

                logger.exception(
                    "IDE lease renewal failed: %s",
                    self.request_id,
                )

                return

    def start(self):
        """Start renewing the lease in a background thread."""

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
        """Stop renewal and wait briefly for the thread."""

        # Request the background thread to stop.
        self._stop_event.set()

        if self._thread is not None:

            # Avoid waiting forever for a blocked database call.
            self._thread.join(
                timeout=self.STOP_TIMEOUT_SECONDS
            )

            # If the thread is still running, treat the
            # lease as unsafe for further operations.
            if self._thread.is_alive():

                self._lost_lease.set()

                logger.error(
                    "IDE lease heartbeat did not stop: %s",
                    self.request_id,
                )