import uuid

from django.conf import settings
from django.db import models


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
