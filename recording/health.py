"""
ffprobe-based health check for recorded videos.

Used in two places:
  1. recording.views.phone_finalize runs it immediately after concatenating
     chunks, so the user gets an instant warning in the UI for bad recordings.
  2. The fix_video_extensions / scan_video_health management commands run
     it across existing videos to retroactively flag the broken ones.
"""
import json
import logging
import os
import shutil
import subprocess
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def probe(file_path: str) -> Optional[dict]:
    """Run ffprobe and return parsed JSON or None on failure."""
    if not _ffprobe_available():
        return None
    if not os.path.isfile(file_path):
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-print_format", "json",
                "-show_format", "-show_streams",
                file_path,
            ],
            capture_output=True, text=True, timeout=20,
        )
    except subprocess.TimeoutExpired:
        return None
    except OSError as e:
        logger.warning("ffprobe invocation failed: %s", e)
        return None

    if result.returncode != 0 or not result.stdout:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def classify_file(file_path: str, expect_audio: bool = True) -> Tuple[str, float, str]:
    """
    Inspect *file_path* with ffprobe and return (health_status, duration_seconds, detail).

    health_status is one of the Video.HEALTH_* string constants.
    duration_seconds is 0.0 when unknown.
    detail is a short human-readable note suitable for storing on the row.
    """
    # Import here to avoid circular import with projects.models.
    from projects.models import Video

    if not _ffprobe_available():
        return Video.HEALTH_UNKNOWN, 0.0, "ffprobe not installed on server"
    if not os.path.isfile(file_path):
        return Video.HEALTH_CORRUPTED, 0.0, "file missing on disk"

    size = os.path.getsize(file_path)
    if size < 1024:
        return Video.HEALTH_EMPTY, 0.0, f"file is only {size} bytes"

    data = probe(file_path)
    if data is None:
        return Video.HEALTH_CORRUPTED, 0.0, "ffprobe could not parse the container"

    streams = data.get("streams") or []
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    try:
        duration = float(data.get("format", {}).get("duration", "0") or 0)
    except (TypeError, ValueError):
        duration = 0.0

    if not streams:
        return Video.HEALTH_EMPTY, duration, "no streams detected"

    if not video_streams and audio_streams:
        return (
            Video.HEALTH_AUDIO_ONLY,
            duration,
            f"audio-only ({audio_streams[0].get('codec_name', '?')}), no video track",
        )

    if not video_streams:
        return Video.HEALTH_EMPTY, duration, "no video or audio stream"

    if duration < 0.5:
        return (
            Video.HEALTH_EMPTY,
            duration,
            f"duration is only {duration:.2f}s",
        )

    if expect_audio and not audio_streams:
        # Video-only when audio was supposed to be enabled — flag as a warning
        # but treat as OK (still playable).
        return Video.HEALTH_OK, duration, "no audio stream (audio was expected)"

    return Video.HEALTH_OK, duration, ""


def update_video_health(video) -> str:
    """Probe *video*'s file, update health_status / detail / duration, save, return new status."""
    from django.utils import timezone

    if not video.file:
        video.health_status = video.HEALTH_CORRUPTED
        video.health_detail = "no file attached"
        video.health_checked_at = timezone.now()
        video.save(update_fields=["health_status", "health_detail", "health_checked_at"])
        return video.health_status

    file_path = video.file.path
    status, duration, detail = classify_file(file_path)

    video.health_status = status
    video.health_detail = detail
    video.health_checked_at = timezone.now()
    if duration > 0 and (video.duration_seconds is None or video.duration_seconds == 0):
        video.duration_seconds = duration
        video.save(update_fields=[
            "health_status", "health_detail", "health_checked_at", "duration_seconds",
        ])
    else:
        video.save(update_fields=["health_status", "health_detail", "health_checked_at"])
    return status
