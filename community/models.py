"""Database models for CodeView groups, memberships, and collaboration."""

# Generate unique identifiers for each membership period.
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


# ============================================================
# 1. PROJECT GROUP
# ============================================================

class ProjectGroup(models.Model):
    """Store public communities and private development groups."""

    # Rules for joining a group.
    JOIN_POLICY_CHOICES = [
        ("open", "Anyone Can Join"),
        ("approval", "Admin Approval Required"),
        ("invite", "Invitation Only"),
    ]

    name = models.CharField(max_length=100)

    description = models.TextField()

    # Developer who created the group.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_project_groups",
    )

    # False = Public Community.
    # True = Private Development Group.
    is_private = models.BooleanField(default=False)

    # Determines how developers become members.
    join_policy = models.CharField(
        max_length=10,
        choices=JOIN_POLICY_CHOICES,
        default="open",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        """Prevent private groups from allowing unrestricted joining."""

        super().clean()

        if self.is_private and self.join_policy == "open":
            raise ValidationError({
                "join_policy": (
                    "Private development groups must require "
                    "admin approval or an invitation."
                )
            })

    def save(self, *args, **kwargs):
        """Validate group settings before saving."""

        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


# ============================================================
# 2. GROUP MEMBER
# ============================================================

class GroupMember(models.Model):
    """Store approved group members and their roles."""

    ROLE_CHOICES = [
        ("admin", "Admin"),
        ("member", "Member"),
    ]

    group = models.ForeignKey(
        ProjectGroup,
        on_delete=models.CASCADE,
        related_name="memberships",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )

    role = models.CharField(
        max_length=10,
        choices=ROLE_CHOICES,
        default="member",
    )

       # Record when the developer joined the group.
    joined_at = models.DateTimeField(auto_now_add=True)

    # Give each membership period a unique identifier.
    #
    # Example:
    # John joins Group 5 -> Generation A
    # John leaves Group 5 -> Generation A becomes invalid
    # John rejoins Group 5 -> Generation B
    #
    # Old IDE sessions tied to Generation A must not become
    # valid again when John rejoins.
    membership_generation = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
    )

    class Meta:
        constraints = [
            # A developer cannot join the same group twice.
            models.UniqueConstraint(
                fields=["group", "user"],
                name="unique_project_group_member",
            ),
        ]

    def clean(self):
        """Prevent private groups from exceeding 10 members."""

        super().clean()

        if not self.group_id:
            return

        # Public communities can have unlimited members.
        if not self.group.is_private:
            return

        # Exclude this membership when editing an existing record.
        members = GroupMember.objects.filter(
            group_id=self.group_id,
        )

        if self.pk:
            members = members.exclude(pk=self.pk)

        if members.count() >= 10:
            raise ValidationError(
                "Private development groups cannot exceed 10 members."
            )

    def save(self, *args, **kwargs):
        """Validate membership before saving."""

        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} in {self.group.name}"


# ============================================================
# 3. GROUP MEMBERSHIP REQUEST
# ============================================================

class GroupMembershipRequest(models.Model):
    """Store requests from developers who want to join groups."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    # Group the developer wants to join.
    group = models.ForeignKey(
        ProjectGroup,
        on_delete=models.CASCADE,
        related_name="membership_requests",
    )

    # Developer requesting membership.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_join_requests",
    )

    # New requests must be reviewed.
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default="pending",
    )

    # Optional introduction from the developer.
    message = models.TextField(
        max_length=1000,
        blank=True,
    )

    # Administrator who reviewed the request.
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_group_requests",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]

        constraints = [
            models.UniqueConstraint(
                fields=["group", "user"],
                name="unique_group_membership_request",
            ),
        ]

        indexes = [
            models.Index(
                fields=["group", "status", "created_at"],
                name="group_request_status_idx",
            ),
        ]

    def clean(self):
        """Check whether the developer can request membership."""

        super().clean()

        if not self.group_id or not self.user_id:
            return

        already_member = GroupMember.objects.filter(
            group_id=self.group_id,
            user_id=self.user_id,
        ).exists()

        if already_member:
            raise ValidationError(
                "This developer is already a member of the group."
            )

        if self.group.join_policy == "invite":
            raise ValidationError(
                "This group can only be joined through an invitation."
            )

    def save(self, *args, **kwargs):
        """Validate membership requests before saving."""

        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.user} requested to join "
            f"{self.group.name}: {self.status}"
        )


# ============================================================
# 4. GROUP DISCUSSION
# ============================================================

class GroupDiscussion(models.Model):
    """Store discussion posts inside groups."""

    group = models.ForeignKey(
        ProjectGroup,
        on_delete=models.CASCADE,
        related_name="discussions",
    )

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_discussions",
    )

    title = models.CharField(max_length=200)

    content = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


# ============================================================
# 5. GROUP DISCUSSION COMMENT
# ============================================================

class GroupDiscussionComment(models.Model):
    """Store comments and nested replies under discussion posts."""

    discussion = models.ForeignKey(
        GroupDiscussion,
        on_delete=models.CASCADE,
        related_name="comments",
    )

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )

    content = models.TextField(max_length=2000)

    # NEW:
    # A comment can reply to another comment.
    #
    # parent=None means this is a normal comment.
    # parent=<comment> means this is a reply.
    #
    # "self" means this model references another record
    # from the same database table.
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="replies",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def clean(self):
        """Prevent invalid relationships between comments."""

        super().clean()

        # Normal comments do not have a parent.
        if not self.parent_id:
            return

        # A comment cannot reply to itself.
        if self.pk and self.parent_id == self.pk:
            raise ValidationError(
                "A comment cannot reply to itself."
            )

        # A reply must belong to the same discussion
        # as the comment it is replying to.
        if self.parent.discussion_id != self.discussion_id:
            raise ValidationError(
                "Replies must belong to the same discussion."
            )

        # Track visited comments to prevent circular replies.
        seen = {self.pk} if self.pk else set()

        ancestor = self.parent

        while ancestor is not None:

            if ancestor.pk in seen:
                raise ValidationError(
                    "Comment replies cannot form a cycle."
                )

            seen.add(ancestor.pk)

            ancestor = ancestor.parent

    def save(self, *args, **kwargs):
        """Validate replies before saving them."""

        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Comment by {self.author.username}"


# ============================================================
# 6. GROUP DISCUSSION LIKE
# ============================================================

class GroupDiscussionLike(models.Model):
    """Track which developers liked a discussion."""

    discussion = models.ForeignKey(
        GroupDiscussion,
        on_delete=models.CASCADE,
        related_name="likes",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Prevent duplicate likes.
            models.UniqueConstraint(
                fields=["discussion", "user"],
                name="unique_discussion_like",
            ),
        ]

    def __str__(self):
        return f"{self.user} liked {self.discussion.title}"


# ============================================================
# 7. HIDDEN DISCUSSION
# ============================================================

class HiddenDiscussion(models.Model):
    """Remember posts hidden by individual developers."""

    discussion = models.ForeignKey(
        GroupDiscussion,
        on_delete=models.CASCADE,
        related_name="hidden_by",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )

    hidden_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["discussion", "user"],
                name="unique_hidden_discussion",
            ),
        ]

    def __str__(self):
        return f"{self.user} hid {self.discussion.title}"


# ============================================================
# 8. GROUP CODE BOARD
# ============================================================

class GroupCodeBoard(models.Model):
    """Preserve the existing shared code board."""

    # The developer who owns this private Code Board.
    # Null is temporary so existing database records can be migrated.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="private_code_boards",
        null=True,
        blank=True,
    )

    LANGUAGE_CHOICES = [
        ("python", "Python"),
        ("javascript", "JavaScript"),
        ("typescript", "TypeScript"),
        ("go", "Go"),
        ("java", "Java"),
        ("cpp", "C++"),
        ("csharp", "C#"),
        ("php", "PHP"),
        ("html", "HTML"),
        ("css", "CSS"),
        ("sql", "SQL"),
        ("other", "Other"),
    ]

    # Connect each private Code Board to a development group.
    # One group can have multiple Code Boards.
    group = models.ForeignKey(
        ProjectGroup,
        on_delete=models.CASCADE,
        related_name="code_boards",
    )

    title = models.CharField(
        max_length=200,
        default="Untitled Code",
    )

    language = models.CharField(
        max_length=20,
        choices=LANGUAGE_CHOICES,
        default="python",
    )

    code = models.TextField(
        blank=True,
        default="",
    )

    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="edited_code_boards",
    )

    updated_at = models.DateTimeField(auto_now=True)
        # Ensure each developer has only one Code Board per group.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["group", "owner"],
                name="unique_group_developer_board",
            )
        ]

    def __str__(self):
        return f"{self.group.name} - Code Board"


# ============================================================
# 9. GROUP REPOSITORY
# ============================================================

class GroupRepository(models.Model):
    """Store GitHub repository details for private groups."""

    group = models.OneToOneField(
        ProjectGroup,
        on_delete=models.CASCADE,
        related_name="repository",
    )

    github_owner = models.CharField(max_length=100)

    repository_name = models.CharField(max_length=100)

    default_branch = models.CharField(
        max_length=100,
        default="main",
    )

    connected_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        """Allow repositories only in private development groups."""

        super().clean()

        if self.group_id and not self.group.is_private:
            raise ValidationError(
                "Only private development groups can connect repositories."
            )

    def save(self, *args, **kwargs):
        """Validate repository information before saving."""

        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.github_owner}/{self.repository_name}"


# ============================================================
# 10. GROUP CHAT MESSAGE (NEW)
# ============================================================

class GroupChatMessage(models.Model):
    """
    Store messages sent inside public and private groups.

    Supports:
    - Text messages
    - Code snippets
    - Voice notes
    - File attachments
    - Replies to messages
    - Message editing
    - Soft deletion
    """

    # Types of messages developers can send.
    MESSAGE_TYPES = [
        ("text", "Text"),
        ("code", "Code Snippet"),
        ("voice", "Voice Note"),
        ("file", "Attachment"),
    ]

    # Group where the message was sent.
    group = models.ForeignKey(
        ProjectGroup,
        on_delete=models.CASCADE,
        related_name="chat_messages",
    )

    # Developer who sent the message.
    #
    # SET_NULL keeps chat history when a user account
    # is deleted.
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="group_chat_messages",
    )

    # Identify the type of message.
    message_type = models.CharField(
        max_length=10,
        choices=MESSAGE_TYPES,
        default="text",
    )

    # Stores ordinary text or programming code.
    content = models.TextField(
        blank=True,
    )

    # Programming language used in a code snippet.
    code_language = models.CharField(
        max_length=30,
        blank=True,
    )

    # Store uploaded voice notes or files.
    #
    # Django stores the file path in the database.
    # The actual file is stored using the configured
    # Django file storage system.
    attachment = models.FileField(
        upload_to="group_chat/attachments/%Y/%m/",
        blank=True,
    )

    # Allow a message to reply to another message.
    #
    # Example:
    # John: How do I install Django?
    # Daniel: ↳ Replying to John: pip install django
    reply_to = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_replies",
    )

    # Date the message was created.
    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    # Date the message was last edited.
    edited_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # Soft deletion:
    # Keep the message record but mark it as deleted.
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["created_at", "id"]

        # Helps retrieve messages from a group efficiently.
        indexes = [
            models.Index(
                fields=["group", "created_at", "id"],
            ),
        ]

    def clean(self):
        """Validate chat messages and their reply relationships."""

        super().clean()

        # Validate message replies.
        if self.reply_to_id:

            # A message cannot reply to itself.
            if self.pk and self.reply_to_id == self.pk:
                raise ValidationError(
                    "A message cannot reply to itself."
                )

            # Prevent replies between different groups.
            if self.reply_to.group_id != self.group_id:
                raise ValidationError(
                    "Replies must stay within the same group."
                )

        # Deleted messages remain in the database
        # so existing replies do not lose their references.
        if self.deleted_at:
            return

        # Text and code messages must contain content.
        if self.message_type in ("text", "code"):

            if not self.content.strip():
                raise ValidationError(
                    "Text and code messages require content."
                )

        # Voice notes and file messages require attachments.
        if self.message_type in ("voice", "file"):

            if not self.attachment:
                raise ValidationError(
                    "Voice and file messages require an attachment."
                )

    def save(self, *args, **kwargs):
        """Validate the message before saving it."""

        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):

        sender = (
            self.sender.username
            if self.sender
            else "Deleted user"
        )

        return (
            f"{sender} in {self.group.name} "
            f"({self.message_type})"
        )