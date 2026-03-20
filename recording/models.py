import secrets
import uuid

from django.conf import settings
from django.db import models


class RecordingSession(models.Model):
    """
    A QR-code-based recording session that links a desktop browser
    to a phone for remote recording control.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='recording_sessions',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='recording_sessions',
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        default=secrets.token_urlsafe,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Session {self.id} ({self.project})"


class Comparison(models.Model):
    """A head-to-head comparison between two videos in a project."""

    RESULT_CHOICES = [
        ('left', 'Left'),
        ('right', 'Right'),
        ('equal', 'Equal'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='comparisons',
    )
    video_left = models.ForeignKey(
        'projects.Video',
        on_delete=models.CASCADE,
        related_name='comparisons_as_left',
    )
    video_right = models.ForeignKey(
        'projects.Video',
        on_delete=models.CASCADE,
        related_name='comparisons_as_right',
    )
    result = models.CharField(max_length=5, choices=RESULT_CHOICES)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='comparisons',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.video_left} vs {self.video_right}: {self.result}"


class KeybindPreference(models.Model):
    """Per-user keyboard shortcut preferences for the recording UI."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='keybind_preference',
    )
    start_stop_key = models.CharField(max_length=50, default='Space')
    discard_key = models.CharField(max_length=50, default='Escape')

    def __str__(self):
        return f"Keybinds for {self.user}"


class RecordingSettings(models.Model):
    """Per-user recording quality and codec preferences."""

    RESOLUTION_CHOICES = [
        ('4k', '4K'),
        ('1080p', '1080p'),
        ('720p', '720p'),
        ('480p', '480p'),
    ]
    FRAME_RATE_CHOICES = [
        (60, '60 fps'),
        (30, '30 fps'),
        (24, '24 fps'),
    ]
    VIDEO_CODEC_CHOICES = [
        ('vp9', 'VP9'),
        ('vp8', 'VP8'),
        ('h264', 'H.264'),
    ]
    AUDIO_CODEC_CHOICES = [
        ('opus', 'Opus'),
        ('aac', 'AAC'),
    ]
    AUDIO_BITRATE_CHOICES = [
        (128, '128 kbps'),
        (192, '192 kbps'),
        (256, '256 kbps'),
        (320, '320 kbps'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='recording_settings',
    )
    video_resolution = models.CharField(
        max_length=10, choices=RESOLUTION_CHOICES, default='1080p',
    )
    frame_rate = models.IntegerField(choices=FRAME_RATE_CHOICES, default=30)
    video_codec = models.CharField(
        max_length=10, choices=VIDEO_CODEC_CHOICES, default='vp9',
    )
    audio_enabled = models.BooleanField(default=True)
    audio_codec = models.CharField(
        max_length=10, choices=AUDIO_CODEC_CHOICES, default='opus',
    )
    audio_bitrate = models.IntegerField(
        choices=AUDIO_BITRATE_CHOICES, default=192,
    )

    class Meta:
        verbose_name = 'Recording Settings'
        verbose_name_plural = 'Recording Settings'

    def __str__(self):
        return f"Recording settings for {self.user}"
