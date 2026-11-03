"""Tests for the CodeView automatic recovery worker."""

import os
import unittest

from unittest.mock import patch, MagicMock

from server_runner.sandbox_controller.recovery_worker import (
    RecoveryWorker,
    recovery_enabled,
)


class TestRecoveryWorker(unittest.TestCase):
    """Verify recovery worker startup and safety."""

    # --------------------------------------------------------
    # TEST 1: RECOVERY DISABLED BY DEFAULT
    # --------------------------------------------------------

    @patch.dict(os.environ, {}, clear=True)
    def test_recovery_disabled_by_default(self):
        """Recovery must remain disabled without the feature flag."""

        self.assertFalse(recovery_enabled())

    # --------------------------------------------------------
    # TEST 2: EXPLICIT ACTIVATION
    # --------------------------------------------------------

    @patch.dict(
        os.environ,
        {"CODEVIEW_AUTO_RECOVERY_ENABLED": "true"},
    )
    def test_recovery_requires_explicit_activation(self):
        """Only an explicit true value enables recovery."""

        self.assertTrue(recovery_enabled())

    # --------------------------------------------------------
    # TEST 3: INVALID FLAG VALUES
    # --------------------------------------------------------

    def test_invalid_flag_values_remain_disabled(self):
        """Unexpected configuration values must not enable recovery."""

        for value in ["false", "1", "yes", "enabled", ""]:
            with self.subTest(value=value):
                with patch.dict(
                    os.environ,
                    {"CODEVIEW_AUTO_RECOVERY_ENABLED": value},
                ):
                    self.assertFalse(recovery_enabled())

    # --------------------------------------------------------
    # TEST 4: DISABLED WORKER DOES NOT RUN RECOVERY
    # --------------------------------------------------------

    @patch.dict(
        os.environ,
        {"CODEVIEW_AUTO_RECOVERY_ENABLED": "false"},
    )
    def test_disabled_worker_does_not_run_recovery(self):
        """A disabled worker must never start a recovery cycle."""

        worker = RecoveryWorker()

        with patch.object(worker, "run_once") as run_once:
            worker.run()

        run_once.assert_not_called()

    # --------------------------------------------------------
    # TEST 5: ONE RECOVERY CYCLE
    # --------------------------------------------------------

    @patch(
        "server_runner.sandbox_controller.recovery_worker."
        "create_controller_session_factory"
    )
    @patch(
        "server_runner.sandbox_controller.recovery_worker."
        "SandboxRecoveryService"
    )
    def test_run_once_calls_recovery(
        self,
        mock_recovery_service,
        mock_session_factory,
    ):
        """One cycle should call the existing recovery service."""

        mock_db = MagicMock()

        mock_session_factory.return_value.return_value.__enter__.return_value = (
            mock_db
        )

        mock_service = mock_recovery_service.return_value
        mock_service.recover_pending_operations.return_value = []

        worker = RecoveryWorker()
        worker.run_once()

        mock_service.recover_pending_operations.assert_called_once_with(
            older_than_minutes=5
        )

        mock_db.close.assert_not_called()

        # The context manager handles session cleanup.
        mock_session_factory.return_value.return_value.__exit__.assert_called_once()

    # --------------------------------------------------------
    # TEST 6: SHUTDOWN REQUEST
    # --------------------------------------------------------

    def test_stop_sets_shutdown_event(self):
        """Shutdown must prevent future recovery cycles."""

        worker = RecoveryWorker()

        self.assertFalse(worker.stop_event.is_set())

        worker.stop()

        self.assertTrue(worker.stop_event.is_set())


    # --------------------------------------------------------
    # TEST 7: SHUTDOWN DURING RECOVERY
    # --------------------------------------------------------

    @patch.dict(
        os.environ,
        {"CODEVIEW_AUTO_RECOVERY_ENABLED": "true"},
    )
    def test_shutdown_waits_for_current_recovery_cycle(self):
        """Shutdown must not interrupt an active recovery cycle."""

        worker = RecoveryWorker()

        recovery_finished = False

        def simulated_recovery():
            nonlocal recovery_finished

            # Simulate receiving shutdown during recovery.
            worker.stop()

            # Recovery must still finish its current work.
            recovery_finished = True

        with (
            patch.object(worker, "run_once", side_effect=simulated_recovery)
            as mock_run_once,
            patch(
                "server_runner.sandbox_controller.recovery_worker."
                "signal.signal"
            ),
        ):
            worker.run()

        # The current recovery cycle completed.
        self.assertTrue(recovery_finished)

        # Shutdown was requested.
        self.assertTrue(worker.stop_event.is_set())

        # No second recovery cycle started.
        mock_run_once.assert_called_once()

if __name__ == "__main__":
    unittest.main()