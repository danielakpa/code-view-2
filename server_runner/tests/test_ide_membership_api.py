"""Tests for CodeView's internal IDE membership validation API."""

import json

from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from community.models import ProjectGroup, GroupMember


@override_settings(
    CODEVIEW_IDE_MEMBERSHIP_TOKEN="test-membership-secret",
)
class TestIDEMembershipAPI(TestCase):
    """Verify that IDE membership validation fails securely."""

    def setUp(self):
        """Prepare the endpoint and test identities."""

        # STEP 1: Find the internal validation endpoint.
        self.url = reverse(
            "internal_ide_membership_validate"
        )

        # STEP 2: Create a valid membership generation.
        self.generation = uuid4()

        # STEP 3: Define the internal authentication header.
        self.headers = {
            "HTTP_AUTHORIZATION": (
                "Bearer test-membership-secret"
            )
        }

    def send_request(
        self,
        developer_id=12,
        group_id=5,
        membership_generation=None,
        headers=None,
    ):
        """
        Send a membership-validation request.

        The default request contains valid data types.
        """

        # Use the default generation unless one is supplied.
        if membership_generation is None:
            membership_generation = str(self.generation)

        # Build the JSON request body.
        payload = {
            "developer_id": developer_id,
            "group_id": group_id,
            "membership_generation": membership_generation,
        }

        # Send the request to Django.
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
            **(self.headers if headers is None else headers),
        )

    def test_missing_service_token_rejected(self):
        """Reject requests without authentication."""

        response = self.send_request(headers={})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["authorized"])

    def test_invalid_service_token_rejected(self):
        """Reject requests with an incorrect service token."""

        response = self.send_request(
            headers={
                "HTTP_AUTHORIZATION": "Bearer incorrect-secret"
            }
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["authorized"])

    @override_settings(CODEVIEW_IDE_MEMBERSHIP_TOKEN="")
    def test_unconfigured_service_token_rejected(self):
        """Fail closed when the internal token is not configured."""

        response = self.send_request()

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["authorized"])

    def test_invalid_developer_id_rejected(self):
        """Reject invalid developer identities."""

        response = self.send_request(developer_id=True)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["authorized"])

    def test_invalid_group_id_rejected(self):
        """Reject invalid group identities."""

        response = self.send_request(group_id=-1)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["authorized"])

    def test_invalid_membership_uuid_rejected(self):
        """Reject malformed membership generations."""

        response = self.send_request(
            membership_generation="invalid-uuid"
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["authorized"])

    @patch("server_runner.ide_membership_api.ProjectGroup.objects")
    @patch("server_runner.ide_membership_api.get_user_model")
    @patch(
        "server_runner.ide_membership_api."
        "get_developer_ide_membership_generation"
    )
    def test_valid_group_owner_authorized(
        self,
        mock_generation,
        mock_user_model,
        mock_groups,
    ):
        """Authorize an owner with the correct generation."""

        # Simulate an active developer.
        developer = type(
            "Developer",
            (),
            {"pk": 12, "is_authenticated": True},
        )()

        mock_user_model.return_value.objects.filter.return_value.first.return_value = developer

        # Simulate a private group owned by the developer.
        group = type(
            "Group",
            (),
            {
                "pk": 5,
                "owner_id": 12,
                "is_private": True,
            },
        )()

        mock_groups.filter.return_value.first.return_value = group

        # Simulate the trusted service returning the current generation.
        mock_generation.return_value = self.generation

        response = self.send_request()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["authorized"])

        mock_generation.assert_called_once_with(
            user=developer,
            group=group,
        )

    @patch("server_runner.ide_membership_api.ProjectGroup.objects")
    @patch("server_runner.ide_membership_api.get_user_model")
    @patch(
        "server_runner.ide_membership_api."
        "get_developer_ide_membership_generation"
    )
    def test_valid_group_member_authorized(
        self,
        mock_generation,
        mock_user_model,
        mock_groups,
    ):
        """Authorize an approved member with the correct generation."""

        developer = type(
            "Developer",
            (),
            {"pk": 12, "is_authenticated": True},
        )()

        mock_user_model.return_value.objects.filter.return_value.first.return_value = developer

        group = type(
            "Group",
            (),
            {
                "pk": 5,
                "owner_id": 99,
                "is_private": True,
            },
        )()

        mock_groups.filter.return_value.first.return_value = group

        mock_generation.return_value = self.generation

        response = self.send_request()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["authorized"])

    @patch("server_runner.ide_membership_api.ProjectGroup.objects")
    @patch("server_runner.ide_membership_api.get_user_model")
    @patch(
        "server_runner.ide_membership_api."
        "get_developer_ide_membership_generation"
    )
    def test_removed_member_rejected(
        self,
        mock_generation,
        mock_user_model,
        mock_groups,
    ):
        """Reject developers whose membership has been removed."""

        developer = type(
            "Developer",
            (),
            {"pk": 12, "is_authenticated": True},
        )()

        mock_user_model.return_value.objects.filter.return_value.first.return_value = developer

        group = type(
            "Group",
            (),
            {
                "pk": 5,
                "owner_id": 99,
                "is_private": True,
            },
        )()

        mock_groups.filter.return_value.first.return_value = group

        # Simulate Django denying the removed member.
        mock_generation.side_effect = PermissionError(
            "Developer is no longer a member."
        )

        response = self.send_request()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["authorized"])

    @patch("server_runner.ide_membership_api.ProjectGroup.objects")
    @patch("server_runner.ide_membership_api.get_user_model")
    @patch(
        "server_runner.ide_membership_api."
        "get_developer_ide_membership_generation"
    )
    def test_outdated_generation_rejected(
        self,
        mock_generation,
        mock_user_model,
        mock_groups,
    ):
        """Reject an IDE session from a previous membership."""

        developer = type(
            "Developer",
            (),
            {"pk": 12, "is_authenticated": True},
        )()

        mock_user_model.return_value.objects.filter.return_value.first.return_value = developer

        group = type(
            "Group",
            (),
            {
                "pk": 5,
                "owner_id": 99,
                "is_private": True,
            },
        )()

        mock_groups.filter.return_value.first.return_value = group

        # Current database generation differs from the old session.
        mock_generation.return_value = uuid4()

        response = self.send_request()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["authorized"])

    def test_nonexistent_developer_rejected(self):
        """Reject developers who do not exist."""

        response = self.send_request(
            developer_id=999999999
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["authorized"])

    def test_inactive_developer_rejected(self):
        """Reject inactive developers."""

        # Simulate an inactive developer by ensuring no active
        # account is returned by the database query.
        with patch(
            "server_runner.ide_membership_api.get_user_model"
        ) as mock_user_model:

            mock_user_model.return_value.objects.filter.return_value.first.return_value = None

            response = self.send_request()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["authorized"])