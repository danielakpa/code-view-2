"""Handle CodeView groups, memberships, discussions, chat, and workspaces."""


# ============================================================
# 1. PYTHON BUILT-IN MODULES
# ============================================================

# Import HttpResponse to return an HTTP response to the browser.
from django.http import HttpResponse

# Reuse CodeView's Code Board execution authorization.
from server_runner.services import authorize_code_board_execution

# Call the authenticated FastAPI Runner after Django authorizes a board.
from server_runner.client import CodeExecutionRunnerClient, RunnerUnavailableError

# Used to convert JSON data into Python dictionaries and lists.
import json

# Used to safely encode values when building URLs.
from urllib.parse import quote

# Used to handle errors when making HTTP requests.
from urllib.error import HTTPError, URLError

# Used to send HTTP requests to external services like GitHub.
from urllib.request import Request, urlopen


# ============================================================
# 2. DJANGO AUTHENTICATION
# ============================================================

# Gets the user model configured for this Django project.
from django.contrib.auth import get_user_model

# Restricts access to views so only logged-in users can use them.
from django.contrib.auth.decorators import login_required


# ============================================================
# 3. DJANGO MESSAGES
# ============================================================

# Displays success, warning, and error messages to users.
from django.contrib import messages


# ============================================================
# 4. DJANGO DATABASE
# ============================================================

# Allows multiple database operations to succeed or fail together.
from django.db import transaction



# Q builds complex database filters; Count counts matching records.
from django.db.models import Q, Count


# ============================================================
# 5. DJANGO VALIDATION
# ============================================================

# Used to raise errors when submitted data is invalid.
from django.core.exceptions import ValidationError


# ============================================================
# 6. DJANGO HTTP RESPONSES
# ============================================================

from django.http import (
    # Returns a 403 response when a user has no permission.
    HttpResponseForbidden,

    # Returns a 400 response when a request contains invalid data.
    HttpResponseBadRequest,

    # Sends Python dictionaries or lists as JSON responses.
    JsonResponse,
)


# ============================================================
# 7. DJANGO SHORTCUTS
# ============================================================

from django.shortcuts import (
    # Finds a database object or returns a 404 error.
    get_object_or_404,

    # Redirects the user to another page.
    redirect,

    # Combines an HTML template with data and returns the page.
    render,
)


# ============================================================
# 8. DJANGO URLS
# ============================================================

# Generates a URL using the name of a registered Django route.
from django.urls import reverse


# ============================================================
# 9. DJANGO TIME
# ============================================================

# Provides timezone-aware date and time utilities.
from django.utils import timezone


# ============================================================
# 10. DJANGO REQUEST METHODS
# ============================================================

# Allows a view to accept only GET requests.
from django.views.decorators.http import require_GET

# Allows a view to accept only POST requests.
from django.views.decorators.http import require_POST


# ============================================================
# 11. DJANGO CHANNELS
# ============================================================

# Gets the channel layer used to send real-time WebSocket messages.
from channels.layers import get_channel_layer

# Allows synchronous Django code to call asynchronous functions.
from asgiref.sync import async_to_sync


# ============================================================
# 12. CODEVIEW FORMS
# ============================================================

from .forms import (
    # Validates information when creating or updating a group.
    ProjectGroupForm,

    # Validates information when creating group discussions.
    GroupDiscussionForm,

    # Validates comments submitted to discussions.
    GroupDiscussionCommentForm,

    # Validates chat messages sent by group members.
    GroupChatMessageForm,

    # Validates information when adding members to a group.
    AddGroupMemberForm,

    # Validates GitHub repository URLs before connecting them.
    GroupRepositoryForm,
)


# ============================================================
# 13. CODEVIEW DATABASE MODELS
# ============================================================

from .models import (
    # Represents a project group in CodeView.
    ProjectGroup,

    # Represents a developer who belongs to a group.
    GroupMember,

    # Stores requests from developers who want to join groups.
    GroupMembershipRequest,

    # Stores discussions created inside groups.
    GroupDiscussion,

    # Stores comments made on group discussions.
    GroupDiscussionComment,

    # Stores likes given to group discussions.
    GroupDiscussionLike,

    # Stores discussions hidden from a developer's view.
    HiddenDiscussion,

    # Stores messages sent inside a group's chat.
    GroupChatMessage,

    # Stores code saved on a group's shared Code Board.
    GroupCodeBoard,

    # Stores GitHub repository details connected to a group.
    GroupRepository,
)
# ============================================================
# HELPER: CHECK GROUP MEMBERSHIP
# ============================================================
def is_group_member(group, user):
    """Check whether a developer owns or belongs to a group."""

    # Step 1: Reject users who are not logged in.
    if not user.is_authenticated:
        return False

    # Step 2: Allow the developer who created the group.
    if group.owner_id == user.pk:
        return True

    # Step 3: Check whether the developer is an approved member.
    return GroupMember.objects.filter(
        group=group,
        user=user,
    ).exists()
# ============================================================
# 1. GROUPS DASHBOARD
# ============================================================

@login_required
def groups(request):
    """Display discoverable communities and the user's private groups."""

    member_group_ids = set(
        GroupMember.objects
        .filter(user=request.user)
        .values_list("group_id", flat=True)
    )

    pending_group_ids = set(
        GroupMembershipRequest.objects
        .filter(
            user=request.user,
            status="pending",
        )
        .values_list("group_id", flat=True)
    )

    # Show public communities and private groups the user belongs to.
    project_groups = (
        ProjectGroup.objects
        .filter(
            Q(is_private=False) |
            Q(id__in=member_group_ids)
        )
        .select_related("owner")
        .distinct()
    )

    for group in project_groups:

        group.is_member = group.id in member_group_ids

        group.has_pending_request = (
            group.id in pending_group_ids
        )

    return render(
        request,
        "community/groups.html",
        {
            "groups": project_groups,
        },
    )


# ============================================================
# 2. CREATE GROUP
# ============================================================

@login_required
def create_group(request):
    """Create a group and make its creator an administrator."""

    if request.method == "POST":

        form = ProjectGroupForm(request.POST)

        if form.is_valid():

            with transaction.atomic():

                group = form.save(commit=False)

                group.owner = request.user

                # Private groups cannot allow unrestricted joining.
                if group.is_private and group.join_policy == "open":
                    group.join_policy = "approval"

                group.save()

                # The group creator becomes the first administrator.
                GroupMember.objects.create(
                    group=group,
                    user=request.user,
                    role="admin",
                )

            messages.success(
                request,
                "Your group was created successfully!",
            )

            return redirect("groups")

    else:
        form = ProjectGroupForm()

    return render(
        request,
        "community/create_group.html",
        {
            "form": form,
        },
    )


# ============================================================
# 3. JOIN OR REQUEST TO JOIN A GROUP
# ============================================================

@login_required
@require_POST
def join_group(request, group_id):
    """Join an open group or submit a membership request."""

    redirect_to_group = False

    with transaction.atomic():

        # Lock the group to coordinate membership changes.
        group = get_object_or_404(
            ProjectGroup.objects.select_for_update(),
            id=group_id,
        )

        already_member = is_group_member(
            group,
            request.user,
        )

        if already_member:

            messages.info(
                request,
                "You are already a member of this group.",
            )

            redirect_to_group = True

        elif group.join_policy == "invite":

            return HttpResponseForbidden(
                "This group requires an invitation."
            )

        elif group.join_policy == "open":

            if group.is_private:

                return HttpResponseForbidden(
                    "Private groups cannot allow open joining."
                )

            GroupMember.objects.get_or_create(
                group=group,
                user=request.user,
                defaults={
                    "role": "member",
                },
            )

            messages.success(
                request,
                "You joined the community successfully!",
            )

            redirect_to_group = True

        elif group.join_policy == "approval":

            existing_request = GroupMembershipRequest.objects.filter(
                group=group,
                user=request.user,
            ).first()

            if (
                existing_request is not None
                and existing_request.status == "pending"
            ):

                messages.info(
                    request,
                    "Your membership request is already pending approval.",
                )

            else:

                introduction = request.POST.get(
                    "message",
                    "",
                ).strip()[:1000]

                if existing_request is not None:

                    existing_request.status = "pending"
                    existing_request.message = introduction
                    existing_request.reviewed_by = None
                    existing_request.reviewed_at = None
                    existing_request.save()

                else:

                    GroupMembershipRequest.objects.create(
                        group=group,
                        user=request.user,
                        message=introduction,
                    )

                messages.success(
                    request,
                    (
                        "Membership request sent successfully! "
                        "Please wait for administrator approval."
                    ),
                )

        else:

            return HttpResponseBadRequest(
                "Invalid group joining policy."
            )

    if redirect_to_group:

        return redirect(
            "group_detail",
            group_id=group_id,
        )

    if not group.is_private:

        return redirect(
            "group_detail",
            group_id=group_id,
        )

    return redirect("groups")


# ============================================================
# 4. GROUP DETAILS
# ============================================================
@login_required
def group_detail(request, group_id):
    """
    Display the unified group workspace.

    The workspace contains:
    1. Discussions
    2. Group Chat
    3. Group Members

    Chat messages are loaded only for group members.
    """

    # Find the group or return 404 if it does not exist.
    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    # Check whether the logged-in developer belongs to this group.
    membership = GroupMember.objects.filter(
        group=group,
        user=request.user,
    ).first()

    # Only members can enter private groups.
    if group.is_private and membership is None:
        return HttpResponseForbidden(
            "You must be a member to access this private group."
        )

    # Store membership status for easier checking.
    is_member = membership is not None

    # Load the members of this group.
    members = (
        GroupMember.objects
        .filter(group=group)
        .select_related("user")
        .order_by("joined_at")
    )

    # Check whether the developer has a pending membership request.
    pending_request = GroupMembershipRequest.objects.filter(
        group=group,
        user=request.user,
        status="pending",
    ).exists()

    # Check whether the developer is a group administrator.
    is_admin = (
        is_member
        and membership.role == "admin"
    )

    # ========================================
    # 1. LOAD GROUP DISCUSSIONS
    # ========================================

    # Find discussions hidden by the logged-in developer.
    hidden_discussion_ids = HiddenDiscussion.objects.filter(
        user=request.user,
    ).values_list(
        "discussion_id",
        flat=True,
    )

    # Load the latest 20 discussions.
    discussions = list(
        GroupDiscussion.objects
        .filter(group=group)
        .exclude(id__in=hidden_discussion_ids)
        .select_related("author")
        .annotate(
            like_count=Count("likes", distinct=True),
            comment_count=Count("comments", distinct=True),
        )
        .order_by("-created_at")[:20]
    )

    # Find discussions already liked by this developer.
    liked_discussion_ids = set(
        GroupDiscussionLike.objects
        .filter(
            user=request.user,
            discussion__in=discussions,
        )
        .values_list("discussion_id", flat=True)
    )

    # Mark each discussion as liked or not liked.
    for discussion in discussions:
        discussion.liked_by_user = (
            discussion.id in liked_discussion_ids
        )

    # ========================================
    # 2. LOAD GROUP CHAT
    # ========================================

    # Default values for developers who are not members.
    chat_messages = []
    chat_form = None

    # Only group members can access chat information.
    if is_member:

        # Fetch the latest 50 messages.
        chat_messages = list(
            GroupChatMessage.objects
            .filter(group=group)
            .select_related(
                "sender",
                "reply_to",
                "reply_to__sender",
            )
            .order_by("-created_at", "-id")[:50]
        )

        # Display messages from oldest to newest.
        chat_messages.reverse()

        # Create the form used to send chat messages.
        chat_form = GroupChatMessageForm()

    # ========================================
    # 3. RENDER THE UNIFIED WORKSPACE
    # ========================================

    return render(
        request,
        "community/group_detail.html",
        {
            # Group information.
            "group": group,

            # Membership information.
            "members": members,
            "is_member": is_member,
            "is_admin": is_admin,
            "pending_request": pending_request,

            # Discussion information.
            "discussions": discussions,

            # Chat information.
            "chat_messages": chat_messages,
            "chat_form": chat_form,
        },
    )

# ============================================================
# 5. REVIEW MEMBERSHIP REQUESTS
# ============================================================

@login_required
def membership_requests(request, group_id):
    """Show pending membership requests to administrators."""

    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    is_admin = GroupMember.objects.filter(
        group=group,
        user=request.user,
        role="admin",
    ).exists()

    if not is_admin:

        return HttpResponseForbidden(
            "Only group administrators can review membership requests."
        )

    pending_requests = (
        GroupMembershipRequest.objects
        .filter(
            group=group,
            status="pending",
        )
        .select_related("user")
        .order_by("created_at")
    )

    return render(
        request,
        "community/membership_requests.html",
        {
            "group": group,
            "pending_requests": pending_requests,
        },
    )

    # ============================================================
# ADD GROUP MEMBER
# ============================================================
@login_required
@require_POST
def add_group_member(request, group_id):
    """Allow group administrators to add registered developers."""

    # Redirect back to the Members section.
    members_url = (
        reverse("group_detail", kwargs={"group_id": group_id})
        + "#group-members"
    )

    # Check administrator permission before searching for users.
    group = get_object_or_404(ProjectGroup, id=group_id)

    is_admin = GroupMember.objects.filter(
        group=group,
        user=request.user,
        role="admin",
    ).exists()

    if not is_admin:
        return HttpResponseForbidden(
            "Only group administrators can add members."
        )

    # Validate the submitted username.
    form = AddGroupMemberForm(request.POST)

    if not form.is_valid():
        messages.error(request, "Please enter a valid username.")
        return redirect(members_url)

    # Find the developer by username.
    User = get_user_model()

    developer = User.objects.filter(
        username=form.cleaned_data["username"]
    ).first()

    if developer is None:
        messages.error(request, "Developer not found.")
        return redirect(members_url)

    # Perform membership checks and changes together.
    with transaction.atomic():

        # Lock the group to coordinate membership changes.
        group = get_object_or_404(
            ProjectGroup.objects.select_for_update(),
            id=group_id,
        )

        # Recheck administrator permission while holding the lock.
        is_admin = GroupMember.objects.filter(
            group=group,
            user=request.user,
            role="admin",
        ).exists()

        if not is_admin:
            return HttpResponseForbidden(
                "Only group administrators can add members."
            )

        # Invitation-only groups require an invitation workflow.
        if group.join_policy == "invite":
            messages.error(
                request,
                "Invitation-only groups require an accepted invitation."
            )
            return redirect(members_url)

        # Prevent duplicate membership.
        if GroupMember.objects.filter(
            group=group,
            user=developer,
        ).exists():
            messages.error(
                request,
                "This developer is already a group member."
            )
            return redirect(members_url)

        # Enforce private group membership limit.
        if group.is_private:
            member_count = GroupMember.objects.filter(
                group=group
            ).count()

            if member_count >= 10:
                messages.error(
                    request,
                    "Private groups cannot exceed 10 members."
                )
                return redirect(members_url)

        # Create the membership.
        try:
            GroupMember.objects.create(
                group=group,
                user=developer,
                role="member",
            )
        except ValidationError:
            messages.error(
                request,
                "Unable to add this developer to the group."
            )
            return redirect(members_url)

    messages.success(
        request,
        f"{developer.username} was added successfully!"
    )

    # Stay on the Members section.
    return redirect(members_url)
# ============================================================
# GROUP MEMBERSHIP MANAGEMENT
# ============================================================
@login_required
@require_POST
def remove_group_member(request, group_id, member_id):
    """Allow authorized administrators to remove group members."""

    # Redirect back to the Members section.
    members_url = (
        reverse("group_detail", kwargs={"group_id": group_id})
        + "#group-members"
    )

    with transaction.atomic():

        # Lock the group while changing its membership.
        group = get_object_or_404(
            ProjectGroup.objects.select_for_update(),
            id=group_id,
        )

        # Find the administrator performing this action.
        actor = GroupMember.objects.filter(
            group=group,
            user=request.user,
            role="admin",
        ).first()

        if actor is None:
            return HttpResponseForbidden(
                "Only group administrators can remove members."
            )

        # Find the membership being removed.
        target = get_object_or_404(
            GroupMember.objects.select_for_update(),
            id=member_id,
            group=group,
        )

        # Never remove the group owner.
        if target.user_id == group.owner_id:
            return HttpResponseForbidden(
                "The group owner cannot be removed."
            )

        # Only the owner can remove another administrator.
        if target.role == "admin" and request.user.id != group.owner_id:
            return HttpResponseForbidden(
                "Only the owner can remove administrators."
            )

        username = target.user.username

        # Delete the membership.
        target.delete()

    messages.success(
        request,
        f"{username} was removed from the group."
    )

    # Stay on the Members section.
    return redirect(members_url)

@login_required
@require_POST
def change_group_member_role(request, group_id, member_id):
    """Allow the group owner to promote or demote administrators."""

    with transaction.atomic():

        group = get_object_or_404(
            ProjectGroup.objects.select_for_update(),
            id=group_id,
        )

        # Only the owner can manage administrator roles.
        if request.user.id != group.owner_id:
            return HttpResponseForbidden(
                "Only the group owner can change member roles."
            )

        target = get_object_or_404(
            GroupMember.objects.select_for_update(),
            id=member_id,
            group=group,
        )

        # Protect the owner's administrator role.
        if target.user_id == group.owner_id:
            return HttpResponseForbidden(
                "The owner's role cannot be changed."
            )

        # Accept only supported roles.
        new_role = request.POST.get("role", "").strip()

        if new_role not in ("admin", "member"):
            return HttpResponseBadRequest(
                "Invalid membership role."
            )

        # Update the role in the database.
        target.role = new_role
        target.save(update_fields=["role"])

    messages.success(
        request,
        f"{target.user.username} is now a group {new_role}."
    )

    # Return to the Members tab after promoting or demoting.
    return redirect(
        reverse("group_detail", kwargs={"group_id": group_id})
        + "#group-members"
    )

@login_required
@require_POST
def leave_group(request, group_id):
    """Allow members to leave a group voluntarily."""

    with transaction.atomic():

        group = get_object_or_404(
            ProjectGroup.objects.select_for_update(),
            id=group_id,
        )

        membership = GroupMember.objects.filter(
            group=group,
            user=request.user,
        ).first()

        if membership is None:
            return HttpResponseBadRequest(
                "You are not a member of this group."
            )

        # Owners must transfer ownership before leaving.
        if request.user.id == group.owner_id:
            return HttpResponseForbidden(
                "Transfer group ownership before leaving."
            )

        membership.delete()

    messages.success(
        request,
        "You have left the group successfully."
    )

    return redirect("groups")

    
@login_required
@require_GET
def search_group_developers(request, group_id):
    """Search registered developers for group administrators."""

    # Find the group.
    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    # Only administrators can search for developers to add.
    is_admin = GroupMember.objects.filter(
        group=group,
        user=request.user,
        role="admin",
    ).exists()

    if not is_admin:
        return JsonResponse(
            {"error": "Permission denied."},
            status=403,
        )

    # Read the text entered in the search box.
    query = request.GET.get("q", "").strip()

    # Don't search until at least two characters are entered.
    if len(query) < 2:
        return JsonResponse({"users": []})

    # Get the configured Django user model.
    User = get_user_model()

    # Find developers who already belong to this group.
    existing_members = GroupMember.objects.filter(
        group=group,
    ).values_list("user_id", flat=True)

    # Search registered developers by username.
    developers = (
        User.objects
        .filter(username__icontains=query)
        .exclude(id__in=existing_members)
        .order_by("username")[:10]
    )

    # Convert matching developers into JSON.
    results = [
        {
            "id": developer.id,
            "username": developer.username,
        }
        for developer in developers
    ]

    return JsonResponse({"users": results})

# ============================================================
# 6. APPROVE MEMBERSHIP REQUEST
# ============================================================

@login_required
@require_POST
def approve_membership(request, group_id, request_id):
    """Approve a membership request without exceeding group limits."""

    with transaction.atomic():

        group = get_object_or_404(
            ProjectGroup.objects.select_for_update(),
            id=group_id,
        )

        is_admin = GroupMember.objects.filter(
            group=group,
            user=request.user,
            role="admin",
        ).exists()

        if not is_admin:

            return HttpResponseForbidden(
                "Only group administrators can approve members."
            )

        membership_request = get_object_or_404(
            GroupMembershipRequest.objects.select_for_update(),
            id=request_id,
            group=group,
        )

        if membership_request.status != "pending":

            return HttpResponseBadRequest(
                "This request has already been reviewed."
            )

        already_member = GroupMember.objects.filter(
            group=group,
            user=membership_request.user,
        ).exists()

        if already_member:

            return HttpResponseBadRequest(
                "This developer is already a group member."
            )

        if group.is_private:

            member_count = GroupMember.objects.filter(
                group=group,
            ).count()

            if member_count >= 10:

                messages.error(
                    request,
                    "This private development group already has 10 members.",
                )

                return redirect(
                    "membership_requests",
                    group_id=group.id,
                )

        # Approve the request BEFORE creating the membership.
        #
        # This avoids the membership request model rejecting
        # the update because the developer is already a member.
        membership_request.status = "approved"
        membership_request.reviewed_by = request.user
        membership_request.reviewed_at = timezone.now()
        membership_request.save()

        GroupMember.objects.create(
            group=group,
            user=membership_request.user,
            role="member",
        )

        messages.success(
            request,
            (
                f"{membership_request.user.username} "
                "was approved successfully!"
            ),
        )

    return redirect(
        "membership_requests",
        group_id=group_id,
    )


# ============================================================
# 7. REJECT MEMBERSHIP REQUEST
# ============================================================

@login_required
@require_POST
def reject_membership(request, group_id, request_id):
    """Reject a pending membership request."""

    with transaction.atomic():

        group = get_object_or_404(
            ProjectGroup.objects.select_for_update(),
            id=group_id,
        )

        is_admin = GroupMember.objects.filter(
            group=group,
            user=request.user,
            role="admin",
        ).exists()

        if not is_admin:

            return HttpResponseForbidden(
                "Only group administrators can reject requests."
            )

        membership_request = get_object_or_404(
            GroupMembershipRequest.objects.select_for_update(),
            id=request_id,
            group=group,
        )

        if membership_request.status != "pending":

            return HttpResponseBadRequest(
                "This request has already been reviewed."
            )

        membership_request.status = "rejected"
        membership_request.reviewed_by = request.user
        membership_request.reviewed_at = timezone.now()
        membership_request.save()

        messages.success(
            request,
            (
                f"Membership request from "
                f"{membership_request.user.username} was rejected."
            ),
        )

    return redirect(
        "membership_requests",
        group_id=group_id,
    )


# ============================================================
# 8. PRIVATE GROUP MEETING ROOM
# ============================================================

@login_required
def meeting_room(request, group_id):
    """Allow private development group members into their meeting room."""

    # Find the group or return 404 if it doesn't exist.
    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    # Only private groups can access the development room.
    if not group.is_private:
        return HttpResponseForbidden(
            "Meeting rooms are only available to private development groups."
        )

    # Only group members can enter the development room.
    if not is_group_member(group, request.user):
        return HttpResponseForbidden(
            "You must belong to this group to enter its meeting room."
        )

    # Get the group's connected GitHub repository, if one exists.
    repository = GroupRepository.objects.filter(
        group=group,
    ).first()

    # Create the form for connecting a GitHub repository.
    repository_form = GroupRepositoryForm()

    # Check whether the user is the group owner or an admin.
    can_manage_repository = (
        group.owner_id == request.user.id
        or GroupMember.objects.filter(
            group=group,
            user=request.user,
            role="admin",
        ).exists()
    )
        # ============================================================
    # LOAD EXISTING GROUP CHAT MESSAGES
    # ============================================================

    # Get the latest 50 messages belonging to this group.
    recent_messages = list(
        GroupChatMessage.objects
        .filter(group=group)
        .select_related("sender", "reply_to", "reply_to__sender")
        .order_by("-created_at", "-id")[:50]
    )

    # Display messages from oldest to newest.
    recent_messages.reverse()
    # ============================================================
    # RENDER THE MEETING ROOM
    # ============================================================

    # Send the group, repository, and chat messages to the template.
    return render(
        request,
        "community/meeting_room.html",
        {
            "group": group,
            "repository": repository,
            "repository_form": repository_form,
            "can_manage_repository": can_manage_repository,

            # Share the same messages used by Group Chat.
            "chat_messages": recent_messages,
        },
    )

# ============================================================
# 9. PRIVATE GROUP CODE BOARD
# ============================================================

@login_required
def code_board(request, group_id):
    """
    Display the developer's private Code Board.

    Source code is written and executed directly from the browser.
    We do not save temporary source code to PostgreSQL.
    """

    # ========================================================
    # STEP 1: FIND THE DEVELOPMENT GROUP
    # ========================================================

    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    # Code Boards are available only in private groups.
    if not group.is_private:
        return HttpResponseForbidden(
            "Code boards are only available to private development groups."
        )

    # Developers must belong to the group.
    if not is_group_member(group, request.user):
        return HttpResponseForbidden(
            "You must belong to this group to access its code board."
        )

    # ========================================================
    # STEP 2: GET THE DEVELOPER'S PRIVATE CODE BOARD
    # ========================================================

    # Keep a board record for ownership and authorization.
    # The board is no longer used to save temporary source code.
    board, created = GroupCodeBoard.objects.get_or_create(
        group=group,
        owner=request.user,
    )
        # ========================================================
    # STEP 3: PREPARE THE DEVELOPER'S EXECUTION SANDBOX
    # ========================================================

    # Ask the FastAPI Runner to prepare a private sandbox.
    # The developer's group membership has already been verified.
       # ========================================================
    # STEP 3: PREPARE AND START THE DEVELOPER'S DOCKER IDE
    # ========================================================

    try:
        # Create one Runner client for both operations.
        runner_client = CodeExecutionRunnerClient()

        # First, prepare the developer's persistent workspace.
        sandbox = async_to_sync(
            runner_client.prepare_sandbox
        )(
            developer_id=request.user.pk,
            group_id=group.pk,
        )

        # Next, ask the Runner to create or start the Docker IDE.
        ide = async_to_sync(
            runner_client.ensure_ide
        )(
            developer_id=request.user.pk,
            group_id=group.pk,
        )

        # Confirm that the Runner reports the IDE as ready.
        if not ide.get("ready"):
            return HttpResponse(
                "Your development environment is not ready. "
                "Please try again.",
                status=503,
            )

    except RunnerUnavailableError:

        # Do not expose internal service errors or tokens.
        return HttpResponse(
            "CodeView could not start your development environment. "
            "Please try again.",
            status=503,
        )

    # ========================================================
    # STEP 3: DISABLE THE OLD SAVE CODE ENDPOINT
    # ========================================================

    # Reject POST requests to this page.
    # Code execution uses the separate run_code_board endpoint.
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    # ========================================================
    # STEP 4: FIND THE CONNECTED GITHUB REPOSITORY
    # ========================================================

    repository = GroupRepository.objects.filter(
        group=group,
    ).first()

    # Store available GitHub branches.
    branches = []

    # ========================================================
    # STEP 5: RETRIEVE GITHUB BRANCHES
    # ========================================================

    if repository:

        # Build the GitHub API URL.
        github_url = (
            f"https://api.github.com/repos/"
            f"{repository.github_owner}/"
            f"{repository.repository_name}/branches?per_page=100"
        )

        # Create the GitHub API request.
        github_request = Request(
            github_url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "CodeView",
            },
        )

        try:

            # Request branches from GitHub.
            with urlopen(github_request, timeout=10) as response:
                branches_data = json.load(response)

            # Extract branch names.
            branches = [
                branch["name"]
                for branch in branches_data
            ]

        except (
            HTTPError,
            URLError,
            TimeoutError,
            ValueError,
            KeyError,
        ):

            # Keep CodeView working if GitHub is unavailable.
            branches = []

    # ========================================================
    # STEP 6: FALLBACK TO THE DEFAULT BRANCH
    # ========================================================

    if repository and not branches:
        branches = [repository.default_branch]

    # ========================================================
    # STEP 7: DISPLAY THE CODE BOARD
    # ========================================================

    return render(
        request,
        "community/code_board.html",
        {
            "group": group,
            "board": board,
            "repository": repository,
            "branches": branches,
        },
    )

@login_required
@require_POST
def run_code_board(request, group_id):
    """
    Execute code submitted directly from the browser.

    The source code is not saved to PostgreSQL.
    """

    # ========================================================
    # STEP 1: FIND THE DEVELOPER'S CODE BOARD
    # ========================================================

    # A developer can execute only their own Code Board.
    board = GroupCodeBoard.objects.filter(
        group_id=group_id,
        owner=request.user,
    ).first()

    # Reject requests for boards the developer does not own.
    if board is None:
        return JsonResponse(
            {"error": "Code Board not found or access denied."},
            status=404,
        )

    # ========================================================
    # STEP 2: CHECK EXECUTION PERMISSIONS
    # ========================================================

    try:
        authorized_board = authorize_code_board_execution(
            user=request.user,
            board_id=board.pk,
        )

    except PermissionError:
        return JsonResponse(
            {"error": "Code Board not found or access denied."},
            status=403,
        )

    # ========================================================
    # STEP 3: GET CODE DIRECTLY FROM THE BROWSER
    # ========================================================

    # Read the selected language from the POST request.
    language = request.POST.get("language", "").strip().lower()

    # Read the source code without saving it to the database.
    source = request.POST.get("code", "")

    # ========================================================
    # STEP 4: VALIDATE THE LANGUAGE
    # ========================================================

    supported_languages = {
        "python",
        "go",
        "javascript",
    }

    if language not in supported_languages:
        return JsonResponse(
            {
                "error": (
                    "Code execution supports Python, "
                    "Go, and JavaScript."
                )
            },
            status=400,
        )

    # ========================================================
    # STEP 5: VALIDATE SOURCE CODE
    # ========================================================

    # Reject empty code.
    if not source.strip():
        return JsonResponse(
            {"error": "Write some code before running it."},
            status=400,
        )

    # Limit source code to 256 KiB.
    if len(source.encode("utf-8")) > 256 * 1024:
        return JsonResponse(
            {"error": "Source code exceeds the 256 KiB limit."},
            status=400,
        )

    # ========================================================
    # STEP 6: SEND EXECUTION REQUEST TO FASTAPI
    # ========================================================

    # Create the Runner client.
    runner = CodeExecutionRunnerClient()

    try:
        # Django is synchronous, but the Runner client is async.
        # async_to_sync allows us to call it from this view.
        result = async_to_sync(runner.request_execution)(
            # Use trusted server-side IDs, never browser-supplied IDs.
            board_id=authorized_board.pk,
            developer_id=request.user.pk,
            group_id=authorized_board.group_id,

            # Use the current editor contents.
            language=language,
            source=source,
        )

    except RunnerUnavailableError:
        # Hide internal service details from the browser.
        return JsonResponse(
            {"error": "Code execution is temporarily unavailable."},
            status=503,
        )

    # ========================================================
    # STEP 7: RETURN EXECUTION RESULT
    # ========================================================

    # Return stdout, stderr, exit code, and other Runner fields.
    return JsonResponse(
        result,
        status=200,
    )

# ============================================================
# 10. CREATE GROUP DISCUSSION
# ============================================================

@login_required
@require_POST
def create_discussion(request, group_id):
    """Allow group members to create discussion posts."""

    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    # Only members can create discussion posts.
    if not is_group_member(group, request.user):

        return HttpResponseForbidden(
            "You must be a group member to create discussions."
        )

    form = GroupDiscussionForm(request.POST)

    if form.is_valid():

        discussion = form.save(commit=False)

        # Never trust the browser to choose the author or group.
        discussion.group = group
        discussion.author = request.user
        discussion.save()

        messages.success(
            request,
            "Your discussion was posted successfully!",
        )

        return redirect(
            "group_detail",
            group_id=group.id,
        )

    messages.error(
        request,
        "Please enter a valid discussion title and message.",
    )

    return redirect(
        "group_detail",
        group_id=group.id,
    )


# ============================================================
# 11. DISCUSSION DETAILS AND COMMENTS
# ============================================================

@login_required
def discussion_detail(request, group_id, discussion_id):
    """Display a discussion and its comments and replies."""

    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    # Public communities are discoverable, but only members
    # can participate. Private discussions require membership.
    if group.is_private and not is_group_member(group, request.user):

        return HttpResponseForbidden(
            "You must be a member to view this discussion."
        )

    discussion = get_object_or_404(
        GroupDiscussion.objects.select_related("author"),
        id=discussion_id,
        group=group,
    )

    # A hidden discussion should remain hidden from its user.
    is_hidden = HiddenDiscussion.objects.filter(
        discussion=discussion,
        user=request.user,
    ).exists()

    if is_hidden:

        return HttpResponseForbidden(
            "You have hidden this discussion."
        )

    # Load all comments and replies for the discussion.
    #
    # The template will use each comment's parent_id
    # to display nested conversations.
    comments = (
        GroupDiscussionComment.objects
        .filter(discussion=discussion)
        .select_related("author", "parent")
        .order_by("created_at", "-id")
    )

    liked_by_user = GroupDiscussionLike.objects.filter(
        discussion=discussion,
        user=request.user,
    ).exists()

    return render(
        request,
        "community/discussion_detail.html",
        {
            "group": group,
            "discussion": discussion,
            "comments": comments,
            "liked_by_user": liked_by_user,
            "is_member": is_group_member(group, request.user),
            "comment_form": GroupDiscussionCommentForm(),
        },
    )


# ============================================================
# 12. ADD COMMENT OR REPLY
# ============================================================

@login_required
@require_POST
def add_discussion_comment(request, group_id, discussion_id):
    """Allow members to comment or reply to another comment."""

    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    if not is_group_member(group, request.user):

        return HttpResponseForbidden(
            "You must be a group member to comment."
        )

    discussion = get_object_or_404(
        GroupDiscussion,
        id=discussion_id,
        group=group,
    )

    form = GroupDiscussionCommentForm(request.POST)

    # Check whether the submitted comment is valid.
    if not form.is_valid():

        # Return JSON when JavaScript submits the form.
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":

            return JsonResponse(
                {
                    "success": False,
                    "error": "Please enter a valid comment.",
                    "errors": form.errors.get_json_data(),
                },
                status=400,
            )

        # Keep normal form submissions working.
        messages.error(
            request,
            "Please enter a valid comment.",
        )

        return redirect(
            "discussion_detail",
            group_id=group.id,
            discussion_id=discussion.id,
        )

    comment = form.save(commit=False)

    comment.discussion = discussion
    comment.author = request.user

    # Optional parent comment ID.
    #
    # Empty means a normal comment.
    # A valid ID means a reply to another comment.
    parent_id = request.POST.get("parent_id", "").strip()

    if parent_id:

        parent_comment = get_object_or_404(
            GroupDiscussionComment,
            id=parent_id,
            discussion=discussion,
        )

        comment.parent = parent_comment

        # Save the developer's comment in PostgreSQL.
    comment.save()

        # Count all comments and replies in this discussion.
    comment_count = GroupDiscussionComment.objects.filter(
        discussion=discussion
    ).count()

        # Get the Django Channels connection.
    channel_layer = get_channel_layer()

    # Prepare the new comment information.
    event = {
        "type": "discussion.comment",
        "data": {
            "event": "discussion_comment",
            "discussion_id": discussion.id,
            "comment_count": comment_count,
            "comment_id": comment.id,
            "author": request.user.username,
            "content": comment.content,
            "parent_id": comment.parent_id,
            "parent_author": (
                comment.parent.author.username
                if comment.parent_id
                else None
            ),
            "created_at": comment.created_at.strftime(
                "%b %d, %Y - %H:%M"
            ),
        },
    }

    # Send the comment to connected group members.
    def broadcast_comment():
        if channel_layer is not None:
            async_to_sync(channel_layer.group_send)(
                f"group_discussions_{group.id}",
                event,
            )

    # Broadcast after PostgreSQL commits the comment.
    transaction.on_commit(broadcast_comment)

    # Return JSON when JavaScript submits a comment or reply.
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":

        return JsonResponse({
            "success": True,
            "comment_id": comment.id,
        })

    # Keep normal form submissions working.
    return redirect(
        "discussion_detail",
        group_id=group.id,
        discussion_id=discussion.id,
    )

## ============================================================
# 13. LIKE OR UNLIKE DISCUSSION
# ============================================================

@login_required
@require_POST
def toggle_discussion_like(request, group_id, discussion_id):
    """Like or unlike a discussion and broadcast the updated count."""

    # 1. Find the group.
    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    # 2. Check whether the developer belongs to the group.
    if not is_group_member(group, request.user):
        return HttpResponseForbidden(
            "You must be a group member to like discussions."
        )

    # 3. Find the discussion.
    discussion = get_object_or_404(
        GroupDiscussion,
        id=discussion_id,
        group=group,
    )

    # 4. Save or remove the like.
    with transaction.atomic():

        # Get the actual Django User model.
        User = get_user_model()

        # Lock the user's database record.
        # This prevents overlapping Like requests
        # from the same developer.
        User.objects.select_for_update().get(
            pk=request.user.pk
        )

        # Check whether the developer already liked this discussion.
        existing_like = GroupDiscussionLike.objects.filter(
            discussion=discussion,
            user=request.user,
        ).first()

        if existing_like:

            # Remove the existing like.
            existing_like.delete()

            # The developer no longer likes the discussion.
            liked = False

        else:

            # Save a new like.
            GroupDiscussionLike.objects.create(
                discussion=discussion,
                user=request.user,
            )

            # The developer now likes the discussion.
            liked = True

        # Count the total likes after the change.
        like_count = GroupDiscussionLike.objects.filter(
            discussion=discussion,
        ).count()

    # ========================================================
    # 5. BROADCAST THE UPDATED LIKE COUNT
    # ========================================================

    # Get the Django Channels layer.
    channel_layer = get_channel_layer()

    # Prepare the information to send to connected developers.
    event = {
        "type": "discussion.like",
        "data": {
            "event": "discussion_like",
            "discussion_id": discussion.id,
            "like_count": like_count,
        },
    }

    # Send the update to developers viewing this group.
    def broadcast_like():

        # Broadcast only if a channel layer is available.
        if channel_layer is not None:

            # Call the asynchronous Channels method
            # from this synchronous Django view.
            async_to_sync(channel_layer.group_send)(
                f"group_discussions_{group.id}",
                event,
            )

    # Broadcast after the database transaction commits.
    transaction.on_commit(broadcast_like)

    # ========================================================
    # 6. RETURN JSON FOR JAVASCRIPT
    # ========================================================

    # Check whether JavaScript submitted the Like request.
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":

        # Return the developer's Like status and total likes.
        return JsonResponse({
            "liked": liked,
            "like_count": like_count,
        })

    # ========================================================
    # 7. HANDLE NORMAL FORM SUBMISSIONS
    # ========================================================

    # Return to the discussion if JavaScript is not used.
    return redirect(
        "discussion_detail",
        group_id=group.id,
        discussion_id=discussion.id,
    )
# ============================================================
# 14. HIDE DISCUSSION
# ============================================================

@login_required
@require_POST
def hide_discussion(request, group_id, discussion_id):
    """Hide a discussion only for the current developer."""

    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    if not is_group_member(group, request.user):

        return HttpResponseForbidden(
            "You must be a group member to hide discussions."
        )

    discussion = get_object_or_404(
        GroupDiscussion,
        id=discussion_id,
        group=group,
    )

    HiddenDiscussion.objects.get_or_create(
        discussion=discussion,
        user=request.user,
    )

    messages.success(
        request,
        "Discussion hidden from your feed.",
    )

    return redirect(
        "group_detail",
        group_id=group.id,
    )


# ============================================================
# 15. GROUP CHAT ROOM
# ============================================================

@login_required
def group_chat(request, group_id):
    """Display chat history to authorized group members."""

    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    # Both public and private chats require group membership.
    if not is_group_member(group, request.user):

        return HttpResponseForbidden(
            "You must be a group member to access its chat."
        )

    # Load the latest 50 messages.
    #
    # Fetch newest messages first for efficiency,
    # then reverse them for chronological display.
    recent_messages = list(
        GroupChatMessage.objects
        .filter(group=group)
        .select_related("sender", "reply_to")
        .order_by("-created_at", "-id")[:50]
    )

    recent_messages.reverse()

    return render(
        request,
        "community/group_chat.html",
        {
            "group": group,
            "chat_messages": recent_messages,
            "chat_form": GroupChatMessageForm(),
        },
    )


# ============================================================
# 16. SEND GROUP CHAT MESSAGE
# ============================================================
@login_required
@require_POST
def send_group_message(request, group_id):
    """Save a group message and broadcast it through WebSockets."""

    # Find the group or return 404.
    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    # Only group members can send messages.
    if not is_group_member(group, request.user):
        return HttpResponseForbidden(
            "You must be a group member to send messages."
        )

    # Validate the message and uploaded files.
    form = GroupChatMessageForm(
        request.POST,
        request.FILES,
    )

        # ========================================
    # CHECK IF REQUEST COMES FROM JAVASCRIPT
    # ========================================

    # AJAX requests should receive JSON instead of redirects.
    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )

    # ========================================
    # HANDLE INVALID MESSAGES
    # ========================================

    if not form.is_valid():

        # Return an error response to JavaScript.
        if is_ajax:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Your message is invalid.",
                    "details": form.errors.get_json_data(),
                },
                status=400,
            )

        # Keep the original behavior for normal HTML forms.
        messages.error(
            request,
            "Your message is invalid. Check the content or attachment.",
        )

        return redirect(
            reverse(
                "group_detail",
                kwargs={"group_id": group.id},
            ) + "#group-chat"
        )

    # ========================================
    # CREATE MESSAGE
    # ========================================

    # Create the message without saving immediately.
    chat_message = form.save(commit=False)

    # Assign the group and sender.
    chat_message.group = group
    chat_message.sender = request.user

    # ========================================
    # HANDLE MESSAGE REPLIES
    # ========================================

    reply_to_id = request.POST.get(
        "reply_to_id",
        "",
    ).strip()

    if reply_to_id:

        # Make sure the original message belongs to this group.
        reply_to_message = get_object_or_404(
            GroupChatMessage,
            id=reply_to_id,
            group=group,
        )

        chat_message.reply_to = reply_to_message

    # ========================================
    # SAVE MESSAGE
    # ========================================

    # Save the completed message to the database.
    chat_message.save()

    # ========================================
    # BROADCAST MESSAGE THROUGH WEBSOCKETS
    # ========================================

    # Get Django Channels' communication layer.
    channel_layer = get_channel_layer()

    # This must match the group name in consumers.py.
    room_group_name = f"group_chat_{group.id}"

    # Prepare the data that connected browsers will receive.
    message_data = {
        "type": "message_created",
        "message_id": chat_message.id,
        "group_id": group.id,
        "sender_id": request.user.id,
        "sender": request.user.username,
        "content": chat_message.content,
        "message_type": chat_message.message_type,
        "created_at": chat_message.created_at.isoformat(),
        "reply_to_id": chat_message.reply_to_id,
        "attachment_url": (
            chat_message.attachment.url
            if chat_message.attachment
            else None
        ),
    }

    # Send the event after the database transaction commits.
    def broadcast_message():

        async_to_sync(channel_layer.group_send)(
            room_group_name,
            {
                "type": "chat.message",
                "data": message_data,
            },
        )

    transaction.on_commit(broadcast_message)

    # ========================================
    # RETURN JSON FOR AJAX REQUESTS
    # ========================================

    # JavaScript uses this response to confirm success.
    if is_ajax:
        return JsonResponse(
            {
                "success": True,
                "message_id": chat_message.id,
                "message": "Message sent successfully.",
            },
            status=201,
        )

    # ========================================
    # RETURN TO GROUP CHAT
    # ========================================

    # Normal HTML form submissions still redirect.
    return redirect(
        reverse(
            "group_detail",
            kwargs={"group_id": group.id},
        ) + "#group-chat"
    )

@login_required
@require_POST
def edit_group_message(request, group_id, message_id):
    """Edit a message and broadcast the change through WebSockets."""
# DEBUG: Check whether Django recognizes the AJAX request.
    print("EDIT REQUEST RECEIVED")
    print("AJAX HEADER:", request.headers.get("X-Requested-With"))
    # ========================================
    # 1. CHECK IF REQUEST COMES FROM JAVASCRIPT
    # ========================================

    # AJAX requests receive JSON instead of redirects.
# Check whether JavaScript sent this request.
    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )   

    # ========================================
    # 2. FIND THE GROUP
    # ========================================

    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    # Only group members can edit messages.
    if not is_group_member(group, request.user):
        return HttpResponseForbidden(
            "You must be a group member to edit messages."
        )

    # ========================================
    # 3. FIND THE MESSAGE
    # ========================================

    chat_message = get_object_or_404(
        GroupChatMessage,
        id=message_id,
        group=group,
    )

    # Only the sender can edit their message.
    if chat_message.sender_id != request.user.id:
        return HttpResponseForbidden(
            "You can only edit your own messages."
        )

    # ========================================
    # 4. CHECK MESSAGE TYPE
    # ========================================

    # Only text messages can be edited.
    if chat_message.message_type != "text":

        if is_ajax:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Only text messages can be edited.",
                },
                status=400,
            )

        messages.error(
            request,
            "Only text messages can be edited.",
        )

        return redirect(
            reverse(
                "group_detail",
                kwargs={"group_id": group.id},
            ) + "#group-chat"
        )

    # ========================================
    # 5. VALIDATE THE NEW CONTENT
    # ========================================

    new_content = request.POST.get(
        "content",
        "",
    ).strip()

    # Prevent empty messages.
    if not new_content:

        if is_ajax:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Your message cannot be empty.",
                },
                status=400,
            )

        messages.error(
            request,
            "Your message cannot be empty.",
        )

        return redirect(
            reverse(
                "group_detail",
                kwargs={"group_id": group.id},
            ) + "#group-chat"
        )

    # ========================================
    # 6. UPDATE THE MESSAGE
    # ========================================

    # Replace the previous message content.
    chat_message.content = new_content

    # Record when the message was edited.
    chat_message.edited_at = timezone.now()

    # Save both changed fields to PostgreSQL.
    chat_message.save(
        update_fields=[
            "content",
            "edited_at",
        ],
    )

    # ========================================
    # 7. BROADCAST THE EDIT THROUGH WEBSOCKETS
    # ========================================

    # Get the Django Channels communication layer.
    channel_layer = get_channel_layer()

    # Use the same group name as consumers.py.
    room_group_name = f"group_chat_{group.id}"

    # Prepare the updated message information.
    message_data = {
        "type": "message_edited",
        "message_id": chat_message.id,
        "group_id": group.id,
        "content": chat_message.content,
        "edited_at": chat_message.edited_at.isoformat(),
    }

    # Broadcast only after the database transaction commits.
    def broadcast_edit():

        async_to_sync(channel_layer.group_send)(
            room_group_name,
            {
                "type": "chat.message",
                "data": message_data,
            },
        )

    transaction.on_commit(broadcast_edit)

    # ========================================
    # 8. RETURN JSON FOR AJAX REQUESTS
    # ========================================

    if is_ajax:
        return JsonResponse(
            {
                "success": True,
                "message_id": chat_message.id,
                "content": chat_message.content,
                "edited_at": chat_message.edited_at.isoformat(),
                "message": "Message updated successfully.",
            },
            status=200,
        )

    # ========================================
    # 9. RETURN TO GROUP CHAT
    # ========================================

    messages.success(
        request,
        "Message updated successfully.",
    )

    return redirect(
        reverse(
            "group_detail",
            kwargs={"group_id": group.id},
        ) + "#group-chat"
    )

# ============================================================
# 18. DELETE GROUP CHAT MESSAGE
@login_required
@require_POST
def delete_group_message(request, group_id, message_id):
    """Permanently delete a message and broadcast the deletion."""

    # ========================================
    # 1. CHECK IF REQUEST COMES FROM JAVASCRIPT
    # ========================================

    # AJAX requests receive JSON instead of redirects.
    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )

    # ========================================
    # 2. FIND THE GROUP
    # ========================================

    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    # Only group members can delete messages.
    if not is_group_member(group, request.user):
        return HttpResponseForbidden(
            "You must be a group member to delete messages."
        )

    # ========================================
    # 3. FIND THE MESSAGE
    # ========================================

    chat_message = get_object_or_404(
        GroupChatMessage,
        id=message_id,
        group=group,
    )

    # Only the original sender can delete the message.
    if chat_message.sender_id != request.user.id:
        return HttpResponseForbidden(
            "You can only delete your own messages."
        )

    # ========================================
    # 4. SAVE THE MESSAGE ID BEFORE DELETION
    # ========================================

    # We need this ID to tell connected browsers
    # which message should disappear.
    deleted_message_id = chat_message.id

    # ========================================
    # 5. DELETE THE MESSAGE
    # ========================================

    # Permanently remove the message from PostgreSQL.
    chat_message.delete()

    # ========================================
    # 6. BROADCAST DELETION THROUGH WEBSOCKETS
    # ========================================

    # Get the Django Channels communication layer.
    channel_layer = get_channel_layer()

    # Must match the group name in consumers.py.
    room_group_name = f"group_chat_{group.id}"

    # Prepare the deletion event.
    message_data = {
        "type": "message_deleted",
        "message_id": deleted_message_id,
        "group_id": group.id,
    }

    # Broadcast only after the database transaction commits.
    def broadcast_deletion():

        async_to_sync(channel_layer.group_send)(
            room_group_name,
            {
                "type": "chat.message",
                "data": message_data,
            },
        )

    transaction.on_commit(broadcast_deletion)

    # ========================================
    # 7. RETURN JSON FOR AJAX REQUESTS
    # ========================================

    # JavaScript receives confirmation without reloading.
    if is_ajax:
        return JsonResponse(
            {
                "success": True,
                "message_id": deleted_message_id,
                "message": "Message deleted successfully.",
            },
            status=200,
        )

    # ========================================
    # 8. RETURN TO GROUP CHAT
    # ========================================

    # Keep the original behavior for normal HTML forms.
    messages.success(
        request,
        "Message deleted successfully.",
    )

    return redirect(
        reverse(
            "group_detail",
            kwargs={"group_id": group.id},
        ) + "#group-chat"
    )
    # Send the event after the database transaction commits.
    def broadcast_deletion():
        async_to_sync(channel_layer.group_send)(
            room_group_name,
            {
                # Calls chat_message() inside consumers.py.
                "type": "chat.message",

                # Information sent to connected browsers.
                "data": message_data,
            },
        )

    transaction.on_commit(broadcast_deletion)

    # ========================================
    # 6. RETURN TO GROUP CHAT
    # ========================================

    messages.success(
        request,
        "Message deleted successfully.",
    )

    return redirect(
        reverse(
            "group_detail",
            kwargs={"group_id": group.id},
        ) + "#group-chat"
    )

    # ============================================================
# GITHUB REPOSITORY CONNECTION
# ============================================================

@login_required
@require_POST
def connect_group_repository(request, group_id):
    """Connect a verified public GitHub repository to a private group."""

    # Find the group or return 404 if it doesn't exist.
    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    # Only private groups can connect repositories.
    if not group.is_private:
        return HttpResponseForbidden(
            "Repository connections are only available to private groups."
        )

    # Only group admins can connect repositories.
    is_admin = GroupMember.objects.filter(
        group=group,
        user=request.user,
        role="admin",
    ).exists()

    # The group owner must also be allowed to connect repositories.
    if group.owner_id != request.user.id and not is_admin:
        return HttpResponseForbidden(
            "Only group admins can connect repositories."
        )

    # Validate the submitted GitHub URL.
    form = GroupRepositoryForm(request.POST)

    if not form.is_valid():
        messages.error(
            request,
            "Enter a valid GitHub repository URL.",
        )
        return redirect("meeting_room", group_id=group.id)

    # Extract the validated repository details.
    owner = form.cleaned_data["github_owner"]
    repository_name = form.cleaned_data["repository_name"]

    # Request public repository information from GitHub.
    api_url = (
        f"https://api.github.com/repos/{owner}/{repository_name}"
    )

    github_request = Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "CodeView",
        },
    )

    try:
        # Set a timeout so GitHub cannot keep the request waiting forever.
        with urlopen(github_request, timeout=10) as response:
            repository_data = json.load(response)

    except HTTPError as error:
        if error.code == 404:
            message = "GitHub repository not found or not public."
        elif error.code in (403, 429):
            message = "GitHub is temporarily limiting requests. Try again later."
        else:
            message = "GitHub could not verify this repository."

        messages.error(request, message)
        return redirect("meeting_room", group_id=group.id)

    except (URLError, TimeoutError, ValueError, OSError):
        messages.error(
            request,
            "Unable to contact GitHub. Please try again.",
        )
        return redirect("meeting_room", group_id=group.id)

    # Reject private repositories in this first implementation.
    if repository_data.get("private", True):
        messages.error(
            request,
            "Only public repositories are supported for now.",
        )
        return redirect("meeting_room", group_id=group.id)

    # Use the verified repository details returned by GitHub.
    verified_owner = repository_data.get("owner", {}).get("login")
    verified_name = repository_data.get("name")
    default_branch = repository_data.get("default_branch")

    if not all([verified_owner, verified_name, default_branch]):
        messages.error(
            request,
            "GitHub returned incomplete repository information.",
        )
        return redirect("meeting_room", group_id=group.id)

    # Save the repository connection to PostgreSQL.
    with transaction.atomic():

        # Lock the group while saving the connection.
        locked_group = ProjectGroup.objects.select_for_update().get(
            id=group.id,
        )

        # Recheck authorization before changing the repository.
        still_admin = GroupMember.objects.filter(
            group=locked_group,
            user=request.user,
            role="admin",
        ).exists()

        if locked_group.owner_id != request.user.id and not still_admin:
            return HttpResponseForbidden(
                "You no longer have permission to connect repositories."
            )

        # Update an existing connection or create a new one.
        GroupRepository.objects.update_or_create(
            group=locked_group,
            defaults={
                "github_owner": verified_owner,
                "repository_name": verified_name,
                "default_branch": default_branch,
            },
        )

    messages.success(
        request,
        "GitHub repository connected successfully!",
    )

    return redirect(
        "meeting_room",
        group_id=group.id,
    )

    # ============================================================
# GITHUB REPOSITORY FILE EXPLORER
# ============================================================

@login_required
def group_repository_files(request, group_id):
    """Retrieve the connected repository's files from GitHub."""

    # Find the requested development group.
    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    # Only private development groups can access this feature.
    if not group.is_private:
        return HttpResponseForbidden(
            "Repository files are only available to private groups."
        )

    # Only group members can view repository files.
    if not is_group_member(group, request.user):
        return HttpResponseForbidden(
            "You must belong to this group to view repository files."
        )

    # Find the repository connected to this group.
    repository = GroupRepository.objects.filter(
        group=group,
    ).first()

    if repository is None:
        return JsonResponse(
            {"error": "No GitHub repository connected."},
            status=404,
        )

    # Encode the repository details for safe URL construction.
    owner = quote(repository.github_owner, safe="")
    repo_name = quote(repository.repository_name, safe="")
    branch = quote(repository.default_branch, safe="")

    # Request the complete file tree from GitHub.
    api_url = (
        f"https://api.github.com/repos/{owner}/{repo_name}"
        f"/git/trees/{branch}?recursive=1"
    )

    github_request = Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "CodeView",
        },
    )

    try:
        # Retrieve repository information from GitHub.
        with urlopen(github_request, timeout=10) as response:
            repository_data = json.load(response)

    except HTTPError as error:
        if error.code == 404:
            message = "Repository or branch not found."
        elif error.code in (403, 429):
            message = "GitHub request limit reached. Try again later."
        else:
            message = "Unable to retrieve repository files."

        return JsonResponse(
            {"error": message},
            status=502,
        )

    except (URLError, TimeoutError, ValueError, OSError):
        return JsonResponse(
            {"error": "Unable to contact GitHub."},
            status=502,
        )

    # GitHub may truncate very large repository trees.
    if repository_data.get("truncated"):
        return JsonResponse(
            {
                "error": (
                    "This repository is too large to load completely. "
                    "Folder-by-folder loading will be added later."
                )
            },
            status=422,
        )

    # Extract the files and folders from GitHub's response.
    files = []

    for item in repository_data.get("tree", []):

        # Only include files and directories.
        if item.get("type") not in ("blob", "tree"):
            continue

        files.append(
            {
                "name": item["path"].split("/")[-1],
                "path": item["path"],
                "type": (
                    "folder"
                    if item["type"] == "tree"
                    else "file"
                ),
            }
        )

    # Return the repository files as JSON.
    return JsonResponse(
        {
            "repository": (
                f"{repository.github_owner}/"
                f"{repository.repository_name}"
            ),
            "branch": repository.default_branch,
            "files": files,
        }
    )

    # ============================================================
# GITHUB REPOSITORY FILE CONTENT
# ============================================================

@login_required
@require_GET
def group_repository_file_content(request, group_id):
    """Retrieve the contents of a file from the connected GitHub repository."""

    # Find the development group.
    group = get_object_or_404(
        ProjectGroup,
        id=group_id,
    )

    # Only private groups can use the Development Room.
    if not group.is_private:
        return HttpResponseForbidden(
            "Repository files are only available to private groups."
        )

    # Only group members can read repository files.
    if not is_group_member(group, request.user):
        return HttpResponseForbidden(
            "You must belong to this group to view repository files."
        )

    # Find the GitHub repository connected to this group.
    repository = GroupRepository.objects.filter(
        group=group,
    ).first()

    if repository is None:
        return JsonResponse(
            {"error": "No GitHub repository connected."},
            status=404,
        )

    # Get the selected file path from the browser.
    file_path = request.GET.get("path", "")

    # Reject empty or invalid file paths.
    if (
        not file_path
        or file_path.startswith("/")
        or "\\" in file_path
        or any(
            part in ("", ".", "..")
            for part in file_path.split("/")
        )
    ):
        return JsonResponse(
            {"error": "Invalid repository file path."},
            status=400,
        )

    # Encode repository information for the GitHub API URL.
    owner = quote(repository.github_owner, safe="")
    repo_name = quote(repository.repository_name, safe="")
    branch = quote(repository.default_branch, safe="")

    # Encode each part of the selected file path.
    encoded_path = "/".join(
        quote(part, safe="")
        for part in file_path.split("/")
    )

    # GitHub Contents API returns the file contents.
    api_url = (
        f"https://api.github.com/repos/{owner}/{repo_name}"
        f"/contents/{encoded_path}?ref={branch}"
    )

    github_request = Request(
        api_url,
        headers={
            "Accept": "application/vnd.github.raw+json",
            "User-Agent": "CodeView",
        },
    )

    try:
        # Request the selected file from GitHub.
        with urlopen(github_request, timeout=10) as response:

            # Read at most 1 MiB plus one byte.
            raw_content = response.read(1024 * 1024 + 1)

    except HTTPError as error:
        if error.code == 404:
            message = "File not found in the connected repository."
        elif error.code in (403, 429):
            message = "GitHub request limit reached. Try again later."
        else:
            message = "Unable to retrieve this file."

        return JsonResponse(
            {"error": message},
            status=502,
        )

    except (URLError, TimeoutError, OSError):
        return JsonResponse(
            {"error": "Unable to contact GitHub."},
            status=502,
        )

    # Prevent very large files from overwhelming the editor.
    if len(raw_content) > 1024 * 1024:
        return JsonResponse(
            {"error": "This file is too large to preview."},
            status=413,
        )

    # This first version supports UTF-8 text files.
    try:
        content = raw_content.decode("utf-8")

    except UnicodeDecodeError:
        return JsonResponse(
            {"error": "This file is not supported as a text preview."},
            status=415,
        )

    # Return the source code to the Development Room.
    return JsonResponse(
        {
            "path": file_path,
            "content": content,
            "branch": repository.default_branch,
        }
    )