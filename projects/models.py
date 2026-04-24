import uuid

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone


class Project(models.Model):
    """A video production project owned by a user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects",
    )
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name


class Video(models.Model):
    """A video recording belonging to a project."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="videos",
    )
    file = models.FileField(upload_to="videos/")
    filename_original = models.CharField(max_length=255, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    file_size_bytes = models.BigIntegerField(default=0)
    elo_rating = models.FloatField(default=1500.0)
    comparison_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    thumbnail = models.ImageField(upload_to="thumbnails/", null=True, blank=True)

    class Meta:
        ordering = ["-elo_rating"]

    def __str__(self):
        return self.filename_original or str(self.id)

    def save(self, *args, **kwargs):
        # On first save, ensure the file is stored under videos/<project_id>/<video_id>.webm
        if self.file and not self.file.name.startswith(f"videos/{self.project_id}/"):
            self.file.name = f"videos/{self.project_id}/{self.id}.webm"
        super().save(*args, **kwargs)


class ProjectShare(models.Model):
    """A project shared with a specific user."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="shares",
    )
    shared_with = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shared_projects",
    )
    can_comment = models.BooleanField(
        default=True,
        help_text="Allow the shared user to leave comments on videos.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("project", "shared_with")
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.project.name} → {self.shared_with.username}"


class VideoComment(models.Model):
    """A timestamped comment left on a video."""

    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="video_comments",
    )
    text = models.TextField()
    # Null means the comment is not attached to a specific moment.
    timestamp_seconds = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp_seconds", "created_at"]

    def __str__(self):
        ts = f"@{self.timestamp_seconds:.1f}s" if self.timestamp_seconds is not None else ""
        return f"{self.author.username}{ts}: {self.text[:40]}"


class ShareLink(models.Model):
    """A password-optional public link to a project's ranking or a single video."""

    RANK = "rank"
    VIEW = "view"
    LINK_TYPES = [(RANK, "Ranking"), (VIEW, "View Video")]

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    link_type = models.CharField(max_length=10, choices=LINK_TYPES)

    # Exactly one of project/video is set depending on link_type.
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="share_links",
    )
    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="share_links",
    )

    # Empty string = no password.
    password_hash = models.CharField(max_length=256, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_share_links",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.link_type} link – {self.token}"

    @property
    def has_password(self):
        return bool(self.password_hash)

    def set_password(self, raw_password):
        """Hash and store a new password. Pass '' to remove the password."""
        if raw_password:
            self.password_hash = make_password(raw_password)
        else:
            self.password_hash = ""

    def check_password(self, raw_password):
        """Return True if raw_password matches, or if no password is set."""
        if not self.password_hash:
            return True
        return check_password(raw_password, self.password_hash)

    @property
    def is_expired(self):
        if self.expires_at is None:
            return False
        return timezone.now() > self.expires_at
