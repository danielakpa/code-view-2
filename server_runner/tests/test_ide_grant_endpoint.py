"""Test the FastAPI IDE grant consumption endpoint."""

import os
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase
from fastapi.testclient import TestClient

from server_runner.runner_service.main import (
    app,
    get_db,
)

from server_runner.runner_service.ide_grants import (
    AuthorizedIDEIdentity,
    IDEGrantRejected,
)


class TestIDEGrantEndpoint(SimpleTestCase):
    """Test authentication and IDE grant authorization."""

    def setUp(self):
        """Prepare a test client and authentication token."""

        # STEP 1: Create the FastAPI test client.
        self.client = TestClient(app)

        # STEP 2: Use a temporary Runner authentication token.
        self.token_patch = patch.dict(
            os.environ,
            {"CODEVIEW_RUNNER_TOKEN": "test-runner-secret"},
        )

        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)

        # STEP 3: Create a fake database session.
        self.fake_db = object()

        # STEP 4: Replace the real database dependency.
        app.dependency_overrides[get_db] = lambda: self.fake_db

        # STEP 5: Remove dependency overrides after each test.
        self.addCleanup(app.dependency_overrides.clear)

        # STEP 6: Prepare the endpoint URL.
        self.url = "/internal/ide-grants/consume"

    def test_missing_authentication_rejected(self):
        """Reject requests without a Runner token."""

        response = self.client.post(
            self.url,
            json={"grant": "test-grant"},
        )

        # Requests without authentication must be rejected.
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

        # Incorrect authentication must be rejected.
        self.assertEqual(response.status_code, 401)

    @patch(
        "server_runner.runner_service.main.consume_ide_grant"
    )
    def test_valid_grant_accepted(self, mock_consume):
        """Return all verified identities for an authorized grant."""

        # STEP 1: Prepare the expected developer and group.
        developer_id = 12
        group_id = 5
        sandbox_id = uuid4()

        # STEP 2: Simulate successful grant consumption.
        # The Runner now returns an AuthorizedIDEIdentity object.
        mock_consume.return_value = AuthorizedIDEIdentity(
            developer_id=developer_id,
            group_id=group_id,
            sandbox_id=sandbox_id,
        )

        # STEP 3: Send an authenticated request.
        response = self.client.post(
            self.url,
            headers={
                "Authorization": "Bearer test-runner-secret",
            },
            json={"grant": "valid-test-grant"},
        )

        # STEP 4: Verify the request succeeded.
        self.assertEqual(response.status_code, 200)

        # STEP 5: Verify all three identities are returned.
        self.assertEqual(
            response.json(),
            {
                "authorized": True,
                "developer_id": developer_id,
                "group_id": group_id,
                "sandbox_id": str(sandbox_id),
            },
        )

        # STEP 6: Verify the grant was consumed exactly once.
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

        # Invalid grants must be rejected.
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

        # FastAPI must reject an empty grant.
        self.assertEqual(response.status_code, 422)