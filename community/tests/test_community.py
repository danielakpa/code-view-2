from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

# Import the models from the parent community package.
from community.models import ProjectGroup, GroupMember


class GroupManagementTests(TestCase):
    """Test group removal, role changes, and leaving."""

    def setUp(self):
        User = get_user_model()

        self.owner = User.objects.create_user(
            username="test_owner",
            password="TestPassword123!",
        )

        self.admin = User.objects.create_user(
            username="test_admin",
            password="TestPassword123!",
        )

        self.member = User.objects.create_user(
            username="test_member",
            password="TestPassword123!",
        )

        self.group = ProjectGroup.objects.create(
            name="Test Private Group",
            description="A group for testing.",
            owner=self.owner,
            is_private=True,
            join_policy="approval",
        )

        self.owner_membership = GroupMember.objects.create(
            group=self.group,
            user=self.owner,
            role="admin",
        )

        self.admin_membership = GroupMember.objects.create(
            group=self.group,
            user=self.admin,
            role="admin",
        )

        self.member_membership = GroupMember.objects.create(
            group=self.group,
            user=self.member,
            role="member",
        )

    def test_owner_can_remove_member(self):
        self.client.force_login(self.owner)

        url = reverse(
            "remove_group_member",
            args=[self.group.id, self.member_membership.id],
        )

        response = self.client.post(url)

        self.assertEqual(response.status_code, 302)

        self.assertFalse(
            GroupMember.objects.filter(
                group=self.group,
                user=self.member,
            ).exists()
        )

    def test_admin_cannot_remove_owner(self):
        self.client.force_login(self.admin)

        url = reverse(
            "remove_group_member",
            args=[self.group.id, self.owner_membership.id],
        )

        response = self.client.post(url)

        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_remove_another_admin(self):
        self.client.force_login(self.admin)

        url = reverse(
            "remove_group_member",
            args=[self.group.id, self.owner_membership.id],
        )

        response = self.client.post(url)

        self.assertEqual(response.status_code, 403)

    def test_owner_can_promote_member(self):
        self.client.force_login(self.owner)

        url = reverse(
            "change_group_member_role",
            args=[self.group.id, self.member_membership.id],
        )

        response = self.client.post(
            url,
            {"role": "admin"},
        )

        self.assertEqual(response.status_code, 302)

        self.member_membership.refresh_from_db()

        self.assertEqual(self.member_membership.role, "admin")

    def test_non_owner_cannot_promote_member(self):
        self.client.force_login(self.admin)

        url = reverse(
            "change_group_member_role",
            args=[self.group.id, self.member_membership.id],
        )

        response = self.client.post(
            url,
            {"role": "admin"},
        )

        self.assertEqual(response.status_code, 403)

    def test_member_can_leave_group(self):
        self.client.force_login(self.member)

        url = reverse("leave_group", args=[self.group.id])

        response = self.client.post(url)

        self.assertEqual(response.status_code, 302)

        self.assertFalse(
            GroupMember.objects.filter(
                group=self.group,
                user=self.member,
            ).exists()
        )

    def test_owner_cannot_leave_group(self):
        self.client.force_login(self.owner)

        url = reverse("leave_group", args=[self.group.id])

        response = self.client.post(url)

        self.assertEqual(response.status_code, 403)