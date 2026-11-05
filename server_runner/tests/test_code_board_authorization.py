"""Test authorization for executing private Code Boards."""

from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from server_runner.services import (
    authorize_code_board_execution,
    authorize_private_group_access,
    authorize_developer_ide_access,
)

from django.contrib.auth import get_user_model
from django.test import TestCase

from community.models import (
    ProjectGroup,
    GroupMember,
    GroupCodeBoard,
)


class TestCodeBoardExecutionAuthorization(SimpleTestCase):
    """Verify that only authorized developers can execute Code Boards."""

    def setUp(self):
        """Create fake developers, a group, and a Code Board."""

        # Developer who owns the Code Board.
        self.owner = Mock()
        self.owner.pk = 12
        self.owner.is_authenticated = True

        # Another developer in the same group.
        self.other_developer = Mock()
        self.other_developer.pk = 20
        self.other_developer.is_authenticated = True

        # Developer who has not logged in.
        self.anonymous_user = Mock()
        self.anonymous_user.is_authenticated = False

        # Private development group.
        self.group = Mock()
        self.group.pk = 5
        self.group.is_private = True
        self.group.owner_id = 12

        # Private Code Board belonging to developer 12.
        self.board = Mock()
        self.board.pk = 15
        self.board.owner_id = 12
        self.board.group = self.group

    @patch("server_runner.services.authorize_private_group_access")
    @patch("server_runner.services.GroupCodeBoard.objects")
    def test_owner_can_execute(
        self,
        mock_boards,
        mock_group_access,
    ):
        """Allow the authenticated owner to execute their Code Board."""

        mock_boards.select_related.return_value.filter.return_value.first.return_value = (
            self.board
        )

        result = authorize_code_board_execution(
            user=self.owner,
            board_id=15,
        )

        self.assertEqual(result, self.board)

        mock_group_access.assert_called_once_with(
            user=self.owner,
            group=self.group,
        )

    @patch("server_runner.services.authorize_private_group_access")
    @patch("server_runner.services.GroupCodeBoard.objects")
    def test_other_group_member_cannot_execute(
        self,
        mock_boards,
        mock_group_access,
    ):
        """Reject another developer trying to execute the owner's board."""

        mock_boards.select_related.return_value.filter.return_value.first.return_value = (
            self.board
        )

        with self.assertRaises(PermissionError):
            authorize_code_board_execution(
                user=self.other_developer,
                board_id=15,
            )

    @patch("server_runner.services.authorize_private_group_access")
    @patch("server_runner.services.GroupCodeBoard.objects")
    def test_developer_outside_group_cannot_execute(
        self,
        mock_boards,
        mock_group_access,
    ):
        """Reject developers without permission to access the group."""

        mock_boards.select_related.return_value.filter.return_value.first.return_value = (
            self.board
        )

        # Simulate the group authorization function rejecting access.
        mock_group_access.side_effect = PermissionError(
            "Developer is not a group member."
        )

        with self.assertRaises(PermissionError):
            authorize_code_board_execution(
                user=self.other_developer,
                board_id=15,
            )

    @patch("server_runner.services.GroupCodeBoard.objects")
    def test_anonymous_developer_cannot_execute(self, mock_boards):
        """Reject developers who are not authenticated."""

        with self.assertRaises(PermissionError):
            authorize_code_board_execution(
                user=self.anonymous_user,
                board_id=15,
            )

        # Anonymous users must be rejected before querying the Code Board.
        mock_boards.select_related.assert_not_called()

    @patch("server_runner.services.GroupCodeBoard.objects")
    def test_nonexistent_board_is_rejected(self, mock_boards):
        """Reject Code Boards that do not exist."""

        mock_boards.select_related.return_value.filter.return_value.first.return_value = (
            None
        )

        with self.assertRaises(PermissionError):
            authorize_code_board_execution(
                user=self.owner,
                board_id=999,
            )

class TestDeveloperGroupAuthorization(SimpleTestCase):
    """Verify private-group membership and IDE ownership."""

    def setUp(self):
        """Prepare developers and a private development group."""

        # Create the group owner.
        self.owner = Mock()
        self.owner.pk = 12
        self.owner.is_authenticated = True

        # Create another authenticated developer.
        self.developer = Mock()
        self.developer.pk = 20
        self.developer.is_authenticated = True

        # Create an unauthenticated developer.
        self.anonymous_user = Mock()
        self.anonymous_user.is_authenticated = False

        # Create a private development group.
        self.group = Mock()
        self.group.pk = 5
        self.group.owner_id = 12
        self.group.is_private = True

    @patch("server_runner.services.GroupMember.objects")
    def test_group_owner_has_access(self, mock_members):
        """The group owner can access their private group."""

        mock_members.filter.return_value.exists.return_value = False

        result = authorize_private_group_access(
            self.owner,
            self.group,
        )

        self.assertTrue(result)

    @patch("server_runner.services.GroupMember.objects")
    def test_approved_member_has_access(self, mock_members):
        """An approved group member can access the private group."""

        mock_members.filter.return_value.exists.return_value = True

        result = authorize_private_group_access(
            self.developer,
            self.group,
        )

        self.assertTrue(result)

    @patch("server_runner.services.GroupMember.objects")
    def test_nonmember_is_rejected(self, mock_members):
        """Reject developers who do not belong to the group."""

        mock_members.filter.return_value.exists.return_value = False

        with self.assertRaises(PermissionError):
            authorize_private_group_access(
                self.developer,
                self.group,
            )

    @patch("server_runner.services.GroupMember.objects")
    def test_removed_member_is_rejected(self, mock_members):
        """A developer loses access when membership is removed."""

        # Simulate the developer no longer having a membership record.
        mock_members.filter.return_value.exists.return_value = False

        with self.assertRaises(PermissionError):
            authorize_developer_ide_access(
                self.developer,
                self.group,
                developer_id=20,
            )

    @patch("server_runner.services.GroupMember.objects")
    def test_developer_cannot_access_another_ide(self, mock_members):
        """A group member cannot access another developer's IDE."""

        mock_members.filter.return_value.exists.return_value = True

        with self.assertRaises(PermissionError):
            authorize_developer_ide_access(
                self.developer,
                self.group,
                developer_id=12,
            )

    @patch("server_runner.services.GroupMember.objects")
    def test_developer_can_access_own_ide(self, mock_members):
        """A group member can access their own IDE."""

        mock_members.filter.return_value.exists.return_value = True

        result = authorize_developer_ide_access(
            self.developer,
            self.group,
            developer_id=20,
        )

        self.assertTrue(result)

    @patch("server_runner.services.GroupMember.objects")
    def test_public_group_is_rejected(self, mock_members):
        """Private development features cannot run in public groups."""

        self.group.is_private = False

        with self.assertRaises(PermissionError):
            authorize_private_group_access(
                self.developer,
                self.group,
            )

    @patch("server_runner.services.GroupMember.objects")
    def test_anonymous_developer_is_rejected(self, mock_members):
        """Unauthenticated developers cannot access private groups."""

        with self.assertRaises(PermissionError):
            authorize_private_group_access(
                self.anonymous_user,
                self.group,
            )

        # Authentication must fail before checking membership.
        mock_members.filter.assert_not_called()

class TestCodeBoardDatabaseAuthorization(TestCase):
    """Test Code Board authorization using real database records."""

    def setUp(self):
        """Create developers, a private group, and a Code Board."""

        # Get the user model configured for CodeView.
        User = get_user_model()

        # Create the group owner.
        self.owner = User.objects.create_user(
            username="group_owner_test",
            password="TestPassword123!",
        )

        # Create another developer.
        self.developer = User.objects.create_user(
            username="board_developer_test",
            password="TestPassword123!",
        )

        # Create a private development group.
        self.group = ProjectGroup.objects.create(
            name="Security Test Group",
            description="Testing private Code Board access.",
            owner=self.owner,
            is_private=True,
            join_policy="approval",
        )

        # Approve the developer as a group member.
        self.membership = GroupMember.objects.create(
            group=self.group,
            user=self.developer,
            role="member",
        )

        # Create the developer's private Code Board.
        self.board = GroupCodeBoard.objects.create(
            group=self.group,
            owner=self.developer,
            title="Developer Test Board",
            language="python",
            code='print("Hello CodeView")',
        )

    def test_member_can_execute_own_board(self):
        """An approved developer can execute their own Code Board."""

        board = authorize_code_board_execution(
            user=self.developer,
            board_id=self.board.pk,
        )

        self.assertEqual(board.pk, self.board.pk)

    def test_removed_member_cannot_execute_board(self):
        """Removing membership immediately blocks Code Board execution."""

        # Remove the developer from the private group.
        self.membership.delete()

        # The developer must lose execution permission.
        with self.assertRaises(PermissionError):
            authorize_code_board_execution(
                user=self.developer,
                board_id=self.board.pk,
            )

    def test_group_owner_cannot_execute_another_members_board(self):
        """Even the group owner cannot execute another developer's board."""

        with self.assertRaises(PermissionError):
            authorize_code_board_execution(
                user=self.owner,
                board_id=self.board.pk,
            )

    def test_nonmember_cannot_access_developer_ide(self):
        """A developer outside the group cannot access its IDE."""

        # Remove the developer's membership.
        self.membership.delete()

        with self.assertRaises(PermissionError):
            authorize_developer_ide_access(
                user=self.developer,
                group=self.group,
                developer_id=self.developer.pk,
            )