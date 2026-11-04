"""Test the FastAPI IDE grant consumption endpoint."""

import os
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase
from fastapi.testclient import TestClient

from server_runner.runner_service.main import (
    app,
    get_db,
    verify_runner_token,
)

from server_runner.runner_service.ide_grants import (
    IDEGrantRejected,
)


class TestIDEGrantEndpoint(SimpleTestCase):
    """Test authentication and IDE grant authorization."""

    def setUp(self):
        """Prepare a test client and authentication token."""

        # Create the FastAPI test client.
        self.client = TestClient(app)

        # Use a temporary Runner authentication token.
        self.token_patch = patch.dict(
            os.environ,
            {"CODEVIEW_RUNNER_TOKEN": "test-runner-secret"},
        )

        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)

        # Use a fake database session.
        self.fake_db = object()

        # Replace the real database dependency.
        app.dependency_overrides[get_db] = lambda: self.fake_db

        # Remove the override after each test.
        self.addCleanup(app.dependency_overrides.clear)

        # Prepare the endpoint URL.
        self.url = "/internal/ide-grants/consume"

    def test_missing_authentication_rejected(self):
        """Reject requests without a Runner token."""

        response = self.client.post(
            self.url,
            json={"grant": "test-grant"},
        )

        self.assertEqual(response.status_code, 401)

    def test_invalid_authentication_rejected(self):
        """Reject requests with an incorrect Runner token."""

        response = self.client.post(
            self.url,
            headers={
                "Authorization": "Bearer wrong-token",
            },
            json={"grant": "test-grant"},
        )

        self.assertEqual(response.status_code, 401)

    @patch(
        "server_runner.runner_service.main.consume_ide_grant"
    )
    def test_valid_grant_accepted(self, mock_consume):
        """Return the sandbox UUID for an authorized grant."""

        # Simulate successful grant consumption.
        sandbox_id = uuid4()
        mock_consume.return_value = sandbox_id

        response = self.client.post(
            self.url,
            headers={
                "Authorization": "Bearer test-runner-secret",
            },
            json={"grant": "valid-test-grant"},
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.json(),
            {
                "authorized": True,
                "sandbox_id": str(sandbox_id),
            },
        )

        mock_consume.assert_called_once_with(
            db=self.fake_db,
            token="valid-test-grant",
        )

    @patch(
        "server_runner.runner_service.main.consume_ide_grant"
    )
    def test_invalid_grant_rejected(self, mock_consume):
        """Reject invalid or previously consumed grants."""

        # Simulate an invalid authorization grant.
        mock_consume.side_effect = IDEGrantRejected(
            "IDE authorization denied."
        )

        response = self.client.post(
            self.url,
            headers={
                "Authorization": "Bearer test-runner-secret",
            },
            json={"grant": "invalid-test-grant"},
        )

        self.assertEqual(response.status_code, 403)

        self.assertEqual(
            response.json()["detail"],
            "IDE authorization denied.",
        )

    def test_empty_grant_rejected(self):
        """Reject requests containing an empty grant."""

        response = self.client.post(
            self.url,
            headers={
                "Authorization": "Bearer test-runner-secret",
            },
            json={"grant": ""},
        )

        self.assertEqual(response.status_code, 422)