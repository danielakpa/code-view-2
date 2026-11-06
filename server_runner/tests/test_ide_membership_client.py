"""Tests for the IDE Gateway's Django membership validation client."""

import asyncio

from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import httpx

from django.test import SimpleTestCase

from server_runner.ide_gateway_service.membership_client import (
    MembershipAuthorizationError,
    MembershipServiceUnavailableError,
    validate_ide_membership,
)


class TestIDEMembershipClient(SimpleTestCase):
    """Verify that Gateway membership checks fail securely."""

    def setUp(self):
        """Prepare valid identities and internal service configuration."""

        # STEP 1: Create a valid developer identity.
        self.developer_id = 12

        # STEP 2: Create a valid private group identity.
        self.group_id = 5

        # STEP 3: Create a membership generation.
        self.generation = uuid4()

        # STEP 4: Configure fake internal service credentials.
        self.environment = {
            "CODEVIEW_DJANGO_INTERNAL_URL": (
                "http://django-internal:8000"
            ),
            "CODEVIEW_IDE_MEMBERSHIP_TOKEN": (
                "test-membership-secret"
            ),
        }

    def run_validation(
        self,
        developer_id=None,
        group_id=None,
        membership_generation=None,
    ):
        """Run the asynchronous membership validator."""

        # Use the test identities unless replacements are supplied.
        return asyncio.run(
            validate_ide_membership(
                developer_id=(
                    self.developer_id
                    if developer_id is None
                    else developer_id
                ),
                group_id=(
                    self.group_id
                    if group_id is None
                    else group_id
                ),
                membership_generation=(
                    self.generation
                    if membership_generation is None
                    else membership_generation
                ),
            )
        )

    def create_response(
        self,
        status_code=200,
        data=None,
    ):
        """Create a fake Django HTTP response."""

        if data is None:
            data = {
                "authorized": True,
                "developer_id": self.developer_id,
                "group_id": self.group_id,
            }

        response = Mock()
        response.status_code = status_code
        response.json.return_value = data

        return response

    @patch.dict("os.environ")
    @patch("httpx.AsyncClient")
    def test_valid_membership_authorized(self, mock_client_class):
        """Accept an explicitly authorized membership."""

        # STEP 1: Configure the internal service.
        with patch.dict("os.environ", self.environment):

            # STEP 2: Simulate a successful Django response.
            response = self.create_response()

            # STEP 3: Mock the asynchronous HTTP client.
            client = AsyncMock()
            client.post.return_value = response

            mock_client_class.return_value.__aenter__.return_value = (
                client
            )

            # STEP 4: Validate membership.
            result = self.run_validation()

        # STEP 5: Verify successful authorization.
        self.assertTrue(result)

        # STEP 6: Verify Django received the trusted identities.
        client.post.assert_awaited_once_with(
            (
                "http://django-internal:8000"
                "/internal/ide/membership/validate/"
            ),
            headers={
                "Authorization": (
                    "Bearer test-membership-secret"
                ),
            },
            json={
                "developer_id": 12,
                "group_id": 5,
                "membership_generation": str(
                    self.generation
                ),
            },
        )

    @patch("httpx.AsyncClient")
    def test_removed_member_rejected(self, mock_client_class):
        """Reject a developer removed from the group."""

        with patch.dict("os.environ", self.environment):

            client = AsyncMock()

            client.post.return_value = self.create_response(
                status_code=403,
                data={"authorized": False},
            )

            mock_client_class.return_value.__aenter__.return_value = (
                client
            )

            with self.assertRaises(
                MembershipAuthorizationError
            ):
                self.run_validation()

    def test_invalid_developer_id_rejected(self):
        """Reject boolean developer IDs."""

        with self.assertRaises(
            MembershipAuthorizationError
        ):
            self.run_validation(developer_id=True)

    def test_invalid_group_id_rejected(self):
        """Reject negative group IDs."""

        with self.assertRaises(
            MembershipAuthorizationError
        ):
            self.run_validation(group_id=-1)

    def test_invalid_membership_generation_rejected(self):
        """Reject an empty membership UUID."""

        with self.assertRaises(
            MembershipAuthorizationError
        ):
            self.run_validation(
                membership_generation=UUID(int=0)
            )

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_configuration_rejected(self):
        """Reject requests when Django configuration is missing."""

        with self.assertRaises(
            MembershipServiceUnavailableError
        ):
            self.run_validation()

    @patch("httpx.AsyncClient")
    def test_mismatched_developer_rejected(self, mock_client_class):
        """Reject responses containing another developer's identity."""

        with patch.dict("os.environ", self.environment):

            client = AsyncMock()

            client.post.return_value = self.create_response(
                data={
                    "authorized": True,
                    "developer_id": 99,
                    "group_id": 5,
                }
            )

            mock_client_class.return_value.__aenter__.return_value = (
                client
            )

            with self.assertRaises(
                MembershipServiceUnavailableError
            ):
                self.run_validation()

    @patch("httpx.AsyncClient")
    def test_mismatched_group_rejected(self, mock_client_class):
        """Reject responses containing another group's identity."""

        with patch.dict("os.environ", self.environment):

            client = AsyncMock()

            client.post.return_value = self.create_response(
                data={
                    "authorized": True,
                    "developer_id": 12,
                    "group_id": 99,
                }
            )

            mock_client_class.return_value.__aenter__.return_value = (
                client
            )

            with self.assertRaises(
                MembershipServiceUnavailableError
            ):
                self.run_validation()

    @patch("httpx.AsyncClient")
    def test_invalid_json_rejected(self, mock_client_class):
        """Reject malformed JSON returned by Django."""

        with patch.dict("os.environ", self.environment):

            client = AsyncMock()

            response = self.create_response()

            # Simulate Django returning invalid JSON.
            response.json.side_effect = ValueError(
                "Invalid JSON."
            )

            client.post.return_value = response

            mock_client_class.return_value.__aenter__.return_value = (
                client
            )

            with self.assertRaises(
                MembershipServiceUnavailableError
            ):
                self.run_validation()

    @patch("httpx.AsyncClient")
    def test_django_connection_failure_rejected(
        self,
        mock_client_class,
    ):
        """Reject access when Django cannot be contacted."""

        with patch.dict("os.environ", self.environment):

            client = AsyncMock()

            # Simulate a network connection failure.
            client.post.side_effect = httpx.ConnectError(
                "Connection refused."
            )

            mock_client_class.return_value.__aenter__.return_value = (
                client
            )

            with self.assertRaises(
                MembershipServiceUnavailableError
            ):
                self.run_validation()

    @patch("httpx.AsyncClient")
    def test_unexpected_status_rejected(self, mock_client_class):
        """Reject unexpected HTTP status codes."""

        with patch.dict("os.environ", self.environment):

            client = AsyncMock()

            client.post.return_value = self.create_response(
                status_code=500,
                data={"authorized": False},
            )

            mock_client_class.return_value.__aenter__.return_value = (
                client
            )

            with self.assertRaises(
                MembershipServiceUnavailableError
            ):
                self.run_validation()

    @patch("httpx.AsyncClient")
    def test_explicit_denial_rejected(self, mock_client_class):
        """Reject a 200 response that does not authorize access."""

        with patch.dict("os.environ", self.environment):

            client = AsyncMock()

            client.post.return_value = self.create_response(
                status_code=200,
                data={"authorized": False},
            )

            mock_client_class.return_value.__aenter__.return_value = (
                client
            )

            with self.assertRaises(
                MembershipAuthorizationError
            ):
                self.run_validation()

    @patch("httpx.AsyncClient")
    def test_boolean_response_identity_rejected(
        self,
        mock_client_class,
    ):
        """Reject boolean values masquerading as developer IDs."""

        with patch.dict("os.environ", self.environment):

            client = AsyncMock()

            client.post.return_value = self.create_response(
                data={
                    "authorized": True,
                    "developer_id": True,
                    "group_id": 5,
                }
            )

            mock_client_class.return_value.__aenter__.return_value = (
                client
            )

            with self.assertRaises(
                MembershipServiceUnavailableError
            ):
                self.run_validation()


if __name__ == "__main__":
    import unittest

    unittest.main()