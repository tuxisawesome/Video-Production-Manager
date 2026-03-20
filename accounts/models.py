from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom user model for the Video Production Manager.

    Extends Django's AbstractUser with fields for recording limits and
    tracking which admin created the user.
    """

    max_recording_seconds = models.PositiveIntegerField(
        default=300,
        help_text="Maximum recording duration in seconds per recording. 0 means unlimited.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_users",
        help_text="The admin user who created this account.",
    )

    class Meta:
        ordering = ["-date_joined"]

    def __str__(self):
        return self.username

    @property
    def max_recording_seconds_display(self):
        """Human-readable display of the recording limit."""
        if self.max_recording_seconds == 0:
            return "Unlimited"
        minutes, seconds = divmod(self.max_recording_seconds, 60)
        if minutes and seconds:
            return f"{minutes}m {seconds}s"
        if minutes:
            return f"{minutes}m"
        return f"{seconds}s"


class SiteSettings(models.Model):
    """
    Singleton model for site-wide configuration.

    Only one instance should ever exist. Use SiteSettings.load() to
    retrieve the current settings, creating defaults if necessary.
    """

    max_recordings_per_project = models.PositiveIntegerField(
        default=0,
        help_text="Maximum number of recordings allowed per project. 0 means unlimited.",
    )

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def __str__(self):
        return "Site Settings"

    def save(self, *args, **kwargs):
        # Enforce singleton: always use pk=1.
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Prevent deletion of the singleton instance.
        pass

    @classmethod
    def load(cls):
        """Load the singleton instance, creating it with defaults if needed."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
