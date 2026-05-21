import os
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


class Gallery(models.Model):
    """A gallery of videos within a project."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="galleries",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.project.name} / {self.name}"


class Video(models.Model):
    """A video recording belonging to a gallery."""

    # Health states populated by recording.health.classify_file() (via ffprobe).
    HEALTH_UNKNOWN    = "unknown"
    HEALTH_OK         = "ok"
    HEALTH_AUDIO_ONLY = "audio_only"
    HEALTH_CORRUPTED  = "corrupted"
    HEALTH_EMPTY      = "empty"
    HEALTH_CHOICES = [
        (HEALTH_UNKNOWN,    "Unknown"),
        (HEALTH_OK,         "OK"),
        (HEALTH_AUDIO_ONLY, "Audio only — no video stream"),
        (HEALTH_CORRUPTED,  "Corrupted — container unreadable"),
        (HEALTH_EMPTY,      "Empty — no decodable streams"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    gallery = models.ForeignKey(
        Gallery,
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

    health_status = models.CharField(
        max_length=20, choices=HEALTH_CHOICES, default=HEALTH_UNKNOWN,
        help_text="Result of automated ffprobe health check.",
    )
    health_checked_at = models.DateTimeField(null=True, blank=True)
    health_detail = models.TextField(blank=True, help_text="ffprobe error or note.")

    class Meta:
        ordering = ["-elo_rating"]

    def __str__(self):
        return self.filename_original or str(self.id)

    @property
    def project(self):
        return self.gallery.project

    @property
    def project_id(self):
        return self.gallery.project_id

    @property
    def is_unhealthy(self):
        return self.health_status not in (self.HEALTH_UNKNOWN, self.HEALTH_OK)

    def save(self, *args, **kwargs):
        # On first save, place the file under videos/<project_id>/, preserving
        # whatever extension the recorder produced (mp4 / webm / ogg). The old
        # code forced .webm here which re-corrupted iOS MP4 recordings.
        if self.file and not self.file.name.startswith(f"videos/{self.gallery.project_id}/"):
            ext = os.path.splitext(self.file.name)[1].lstrip(".").lower() or "webm"
            if ext not in ("mp4", "webm", "ogg", "mov", "mkv"):
                ext = "webm"
            self.file.name = f"videos/{self.gallery.project_id}/{self.id}.{ext}"
        super().save(*args, **kwargs)


class ProjectShare(models.Model):
    """A project shared with a specific user."""

    ROLE_CHOICES = [
        ("view", "View only"),
        ("rank", "View + Rank"),
        ("commentator", "Commentator"),
    ]

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
    role = models.CharField(max_length=15, choices=ROLE_CHOICES, default="view")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("project", "shared_with")
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.project.name} → {self.shared_with.username} ({self.role})"


class GalleryShare(models.Model):
    """A gallery shared with a specific user."""

    ROLE_CHOICES = [
        ("view", "View only"),
        ("rank", "View + Rank"),
        ("commentator", "Commentator"),
    ]

    gallery = models.ForeignKey(
        Gallery,
        on_delete=models.CASCADE,
        related_name="shares",
    )
    shared_with = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shared_galleries",
    )
    role = models.CharField(max_length=15, choices=ROLE_CHOICES, default="view")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("gallery", "shared_with")
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.gallery} → {self.shared_with.username} ({self.role})"


class VideoComment(models.Model):
    """A timestamped comment left on a video by an authenticated user or a guest."""

    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    # Authenticated author. Null when the comment came from a public share-link
    # commentator who isn't logged in (guest_name carries their label instead).
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="video_comments",
    )
    guest_name = models.CharField(max_length=80, blank=True)
    text = models.TextField()
    # Null means the comment is not attached to a specific moment.
    timestamp_seconds = models.FloatField(null=True, blank=True)
    # Token of the ShareLink used to post (when posted by a guest). Lets the
    # owner identify which link a problematic comment came in through.
    share_link_token = models.CharField(max_length=64, blank=True, default="")
    # Per-comment opaque token returned only at creation time. The poster
    # caches it client-side (localStorage) and presents it later to edit or
    # delete their own comment — the only ownership signal we have for guests
    # who aren't authenticated.
    edit_token = models.CharField(max_length=32, blank=True, default="")
    edited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp_seconds", "created_at"]

    @property
    def display_author(self):
        if self.author_id:
            return self.author.username
        if self.guest_name:
            return f"{self.guest_name} (guest)"
        return "Anonymous"

    def __str__(self):
        ts = f"@{self.timestamp_seconds:.1f}s" if self.timestamp_seconds is not None else ""
        return f"{self.display_author}{ts}: {self.text[:40]}"


class ShareLink(models.Model):
    """A password-optional public link to a project, gallery, or single video."""

    VIEW = "view"
    RANK = "rank"
    COMMENTATOR = "commentator"

    ACCESS_TYPES = [
        (VIEW, "View only"),
        (RANK, "View + Rank"),
        (COMMENTATOR, "Commentator (View, Rank, Comment, Download)"),
    ]

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    access_type = models.CharField(max_length=15, choices=ACCESS_TYPES, default=VIEW)

    # Exactly one of project/gallery/video is set depending on the link target.
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="share_links",
    )
    gallery = models.ForeignKey(
        Gallery,
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
        return f"{self.access_type} link – {self.token}"

    @property
    def can_view(self):
        return True

    @property
    def can_rank(self):
        return self.access_type in (self.RANK, self.COMMENTATOR)

    @property
    def can_comment(self):
        return self.access_type == self.COMMENTATOR

    @property
    def can_download(self):
        return self.access_type == self.COMMENTATOR

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
