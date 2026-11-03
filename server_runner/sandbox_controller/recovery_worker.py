"""Dedicated background worker for recovering interrupted CodeView IDE operations."""

import logging
import os
import signal
import threading

from server_runner.sandbox_controller.database import (
    create_controller_session_factory,
)

from server_runner.sandbox_controller.recovery import (
    SandboxRecoveryService,
)


# ============================================================
# 1. LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================
# 2. WORKER CONFIGURATION
# ============================================================

# Recovery is disabled unless explicitly enabled.
RECOVERY_ENABLED_VALUE = "true"

# Wait 60 seconds between recovery checks.
RECOVERY_INTERVAL_SECONDS = 60

# Only recover operations older than five minutes.
RECOVERY_AGE_MINUTES = 5


def recovery_enabled() -> bool:
    """Check whether automatic recovery is explicitly enabled."""

    return (
        os.getenv("CODEVIEW_AUTO_RECOVERY_ENABLED", "")
        .strip()
        .lower()
        == RECOVERY_ENABLED_VALUE
    )


# ============================================================
# 3. RECOVERY WORKER
# ============================================================

class RecoveryWorker:
    """Run periodic recovery checks in a dedicated process."""

    def __init__(self):
        # Signal used to stop the worker safely.
        self.stop_event = threading.Event()

    def stop(self, signum=None, frame=None):
        """Request graceful shutdown."""

        logger.info("Recovery worker shutdown requested.")

        # Stop future recovery cycles.
        self.stop_event.set()

    def run_once(self):
        """Run one recovery cycle with its own database session."""

        # Create or reuse the database session factory.
        session_factory = create_controller_session_factory()

        # Open a session for this recovery cycle.
        with session_factory() as db:

            # Reuse the existing recovery service.
            recovery_service = SandboxRecoveryService(db)

            # Recover eligible operations.
            results = recovery_service.recover_pending_operations(
                older_than_minutes=RECOVERY_AGE_MINUTES
            )

            # Record the outcome of each recovery attempt.
            for result in results:
                logger.info(
                    "Recovery result: request=%s operation=%s status=%s message=%s",
                    result.request_id,
                    result.operation,
                    result.status,
                    result.message,
                )

    def run(self):
        """Keep checking for interrupted operations until shutdown."""

        # Never start recovery without explicit permission.
        if not recovery_enabled():
            logger.info(
                "Automatic recovery is disabled. Worker exiting."
            )
            return

        logger.info("CodeView recovery worker started.")

        # Register shutdown signals in the main thread.
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

        # Continue until shutdown is requested.
        while not self.stop_event.is_set():

            try:
                self.run_once()

            except Exception:
                # Log the full error without terminating the worker.
                logger.exception("Recovery cycle failed.")

            # Wait before starting another cycle.
            # The wait ends early if shutdown is requested.
            self.stop_event.wait(RECOVERY_INTERVAL_SECONDS)

        logger.info("CodeView recovery worker stopped.")


# ============================================================
# 4. WORKER ENTRY POINT
# ============================================================

def main():
    """Start the dedicated recovery worker."""

    worker = RecoveryWorker()
    worker.run()


# Only execute when launched directly as a Python module.
# Importing this file from FastAPI will not start recovery.
if __name__ == "__main__":
    main()