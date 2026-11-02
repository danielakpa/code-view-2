"""Tests for the IDE lease heartbeat."""

import unittest

from unittest.mock import MagicMock, patch
from uuid import uuid4

from server_runner.sandbox_controller.lease_heartbeat import (
    IDELeaseHeartbeat,
)


class TestIDELeaseHeartbeat(unittest.TestCase):
    """Verify that IDE operation leases are renewed safely."""

    def setUp(self):
        """Create a heartbeat for each test."""

        self.request_id = uuid4()
        self.lease_owner = uuid4()
        self.lease_generation = 1

        self.heartbeat = IDELeaseHeartbeat(
            request_id=self.request_id,
            lease_owner=self.lease_owner,
            lease_generation=self.lease_generation,
        )

    @patch(
        "server_runner.sandbox_controller.lease_heartbeat."
        "create_controller_session_factory"
    )
    def test_successful_lease_renewal(self, mock_factory):
        """A successful renewal must commit the transaction."""

        # Create a fake database session.
        mock_db = MagicMock()

        # Configure the session context manager.
        mock_factory.return_value.return_value.__enter__.return_value = (
            mock_db
        )

        with patch(
            "server_runner.sandbox_controller.lease_heartbeat."
            "DatabaseReplayStore"
        ) as mock_store_class:

            # Simulate successful lease renewal.
            mock_store = mock_store_class.return_value
            mock_store.renew_lease.return_value = True

            result = self.heartbeat._renew()

            self.assertTrue(result)

            mock_store.renew_lease.assert_called_once_with(
                request_id=self.request_id,
                lease_owner=self.lease_owner,
                lease_generation=self.lease_generation,
            )

            mock_db.commit.assert_called_once()
            mock_db.rollback.assert_not_called()

    @patch(
        "server_runner.sandbox_controller.lease_heartbeat."
        "create_controller_session_factory"
    )
    def test_expired_lease_is_rejected(self, mock_factory):
        """An expired lease must not be renewed."""

        mock_db = MagicMock()

        mock_factory.return_value.return_value.__enter__.return_value = (
            mock_db
        )

        with patch(
            "server_runner.sandbox_controller.lease_heartbeat."
            "DatabaseReplayStore"
        ) as mock_store_class:

            # Simulate PostgreSQL rejecting the renewal.
            mock_store = mock_store_class.return_value
            mock_store.renew_lease.return_value = False

            result = self.heartbeat._renew()

            self.assertFalse(result)

            mock_db.rollback.assert_called_once()
            mock_db.commit.assert_not_called()

    @patch(
        "server_runner.sandbox_controller.lease_heartbeat."
        "create_controller_session_factory"
    )
    def test_database_failure_rolls_back(self, mock_factory):
        """Database errors must roll back the transaction."""

        mock_db = MagicMock()

        mock_factory.return_value.return_value.__enter__.return_value = (
            mock_db
        )

        with patch(
            "server_runner.sandbox_controller.lease_heartbeat."
            "DatabaseReplayStore"
        ) as mock_store_class:

            mock_store = mock_store_class.return_value

            # Simulate a PostgreSQL failure.
            mock_store.renew_lease.side_effect = RuntimeError(
                "Database connection failed."
            )

            with self.assertRaises(RuntimeError):
                self.heartbeat._renew()

            mock_db.rollback.assert_called_once()
            mock_db.commit.assert_not_called()

    def test_heartbeat_starts_and_stops(self):
        """The background thread must stop when requested."""

        # Use a short interval so the test runs quickly.
        self.heartbeat.RENEW_INTERVAL_SECONDS = 0.01

        with patch.object(
            self.heartbeat,
            "_renew",
            return_value=True,
        ) as mock_renew:

            self.heartbeat.start()

            # Wait until the heartbeat has renewed at least once.
            for _ in range(100):
                if mock_renew.called:
                    break

                self.heartbeat._stop_event.wait(0.01)

            self.assertTrue(mock_renew.called)

            self.heartbeat.stop()

            self.assertFalse(self.heartbeat._thread.is_alive())
            self.assertFalse(self.heartbeat.lost_lease)

    def test_failed_renewal_marks_lease_lost(self):
        """A rejected renewal must mark the lease as lost."""

        self.heartbeat.RENEW_INTERVAL_SECONDS = 0.01

        with patch.object(
            self.heartbeat,
            "_renew",
            return_value=False,
        ):
            self.heartbeat.start()

            # Wait for the worker to detect lease loss.
            self.assertTrue(
                self.heartbeat._lost_lease.wait(timeout=1)
            )

            self.heartbeat.stop()

            self.assertTrue(self.heartbeat.lost_lease)

    def test_heartbeat_cannot_start_twice(self):
        """Prevent accidentally starting duplicate heartbeat threads."""

        self.heartbeat.start()

        try:
            with self.assertRaises(RuntimeError):
                self.heartbeat.start()

        finally:
            self.heartbeat.stop()


if __name__ == "__main__":
    unittest.main()