"""Test PostgreSQL database timeout configuration."""

import unittest

from sqlalchemy import text

from sqlalchemy.exc import OperationalError

from server_runner.sandbox_controller.database import (
    create_controller_session_factory,
)


class TestDatabaseTimeout(unittest.TestCase):
    """Verify PostgreSQL timeout settings."""

    def test_database_timeout_settings(self):
        """Confirm PostgreSQL applies the configured timeouts."""

        session_factory = create_controller_session_factory()

        # Create a database session.
        with session_factory() as db:

            # Read the statement timeout.
            statement_timeout = db.execute(
                text("SHOW statement_timeout")
            ).scalar_one()

            # Read the lock timeout.
            lock_timeout = db.execute(
                text("SHOW lock_timeout")
            ).scalar_one()

            # Read the idle transaction timeout.
            idle_timeout = db.execute(
                text(
                    "SHOW idle_in_transaction_session_timeout"
                )
            ).scalar_one()

            # Verify the configured values.
            self.assertEqual(statement_timeout, "30s")

            self.assertEqual(lock_timeout, "5s")

            self.assertEqual(idle_timeout, "1min")



    def test_lock_timeout_with_two_workers(self):
        """A waiting worker must time out when another holds a lock."""

        import uuid

        from sqlalchemy import text
        from sqlalchemy.exc import OperationalError

        session_factory = create_controller_session_factory()

        # Use a unique lock ID so this test does not
        # interfere with other database operations.
        lock_id = uuid.uuid4().int % (2**31)

        # Simulate two independent workers.
        with session_factory() as worker_a, session_factory() as worker_b:

            try:
                # Worker A acquires a transaction-level advisory lock.
                worker_a.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_id)"),
                    {"lock_id": lock_id},
                )

                # Worker B attempts to acquire the same lock.
                # PostgreSQL should cancel this statement
                # after the configured 5-second lock timeout.
                with self.assertRaises(OperationalError):
                    worker_b.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_id)"),
                        {"lock_id": lock_id},
                    )

                # A failed PostgreSQL statement leaves the
                # transaction unusable until it is rolled back.
                worker_b.rollback()

                # Confirm Worker B can execute SQL again.
                result = worker_b.execute(
                    text("SELECT 1")
                ).scalar_one()

                self.assertEqual(result, 1)

            finally:
                # Release the advisory lock and clean up
                # both transactions, even if the test fails.
                worker_b.rollback()
                worker_a.rollback()