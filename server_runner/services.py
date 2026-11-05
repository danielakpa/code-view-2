"""Business logic for CodeView developer terminal sessions."""

# Import Django database transactions.
from django.db import transaction

# Access CodeView's private Code Board model.
from community.models import GroupCodeBoard

# Import CodeView's group membership model.
from community.models import GroupMember

# Import our terminal session model.
from server_runner.models import DeveloperTerminalSession



def authorize_private_group_access(user, group):
    """
    Verify that a developer can access a private development group.

    This function can be reused by:
    - Developer terminals.
    - Code execution.
    - Other private developer features.
    """

    # Step 1: Reject unauthenticated developers.
    if not user.is_authenticated:
        raise PermissionError(
            "You must log in to access this development group."
        )

    # Step 2: Reject public groups.
    if not group.is_private:
        raise PermissionError(
            "This feature requires a private development group."
        )

    # Step 3: Check whether the developer owns the group.
    is_owner = group.owner_id == user.pk

    # Step 4: Check whether the developer is an approved member.
    is_member = GroupMember.objects.filter(
        group=group,
        user=user,
    ).exists()

    # Step 5: Reject unauthorized developers.
    if not is_owner and not is_member:
        raise PermissionError(
            "You do not have permission to access this group."
        )

    # Authorization succeeded.
    return True

def authorize_developer_ide_access(user, group, developer_id):
    """
    Verify that a developer can access their private Docker IDE.

    Security rules:
    1. The developer must belong to the private group.
    2. The developer can access only their own IDE.
    """

    # STEP 1: Reuse CodeView's existing group authorization.
    authorize_private_group_access(
        user=user,
        group=group,
    )

    # STEP 2: Verify that the requested IDE belongs to this developer.
    if user.pk != developer_id:
        raise PermissionError(
            "You cannot access another developer's IDE."
        )

    # STEP 3: Authorization succeeded.
    return True

def get_developer_ide_membership_generation(user, group):
    """
    Return the current membership generation for IDE authorization.

    This allows CodeView to distinguish a developer's current
    group membership from previous memberships.
    """

    # STEP 1: Verify the developer can access the private group.
    authorize_developer_ide_access(
        user=user,
        group=group,
        developer_id=user.pk,
    )

    # STEP 2: Find the developer's current group membership.
    membership = GroupMember.objects.filter(
        group=group,
        user=user,
    ).first()

    # STEP 3: Return the current membership generation.
    if membership is not None:
        return membership.membership_generation

    # STEP 4: Group owners can be authorized without a
    # GroupMember record. Do not invent a generation for them.
    #
    # Owner authorization needs a separate, persistent generation
    # before we can safely issue generation-bound IDE sessions.
    raise PermissionError(
        "IDE membership generation is unavailable."
    )

def authorize_code_board_execution(user, board_id):
    """
    Verify that a developer can execute a private Code Board.

    Checks:
    1. Developer authentication.
    2. Private-group membership.
    3. Code Board ownership.
    """

    # Step 1: Reject unauthenticated developers.
    if not user.is_authenticated:
        raise PermissionError(
            "You must log in to execute code."
        )

    # Step 2: Find the requested Code Board.
    board = GroupCodeBoard.objects.select_related(
        "group"
    ).filter(
        id=board_id
    ).first()

    # Step 3: Reject nonexistent Code Boards.
    if board is None:
        raise PermissionError(
            "Code Board not found or access denied."
        )

    # Step 4: Verify access to the private development group.
    authorize_private_group_access(
        user=user,
        group=board.group,
    )

    # Step 5: Verify that this developer owns the Code Board.
    if board.owner_id != user.pk:
        raise PermissionError(
            "You cannot execute another developer's private Code Board."
        )

    # Step 6: Return the authorized Code Board.
    return board

@transaction.atomic
def create_terminal_session(user, group):
    """Create a terminal session for an authorized developer."""

    # Step 1: Verify the developer's permission.
    authorize_private_group_access(
        user=user,
        group=group,
    )

    # Step 2: Create a terminal session in PostgreSQL.
    session = DeveloperTerminalSession.objects.create(
        developer=user,
        group_id=group.pk,
        status="created",
    )

    # Step 3: Return the created session.
    return session