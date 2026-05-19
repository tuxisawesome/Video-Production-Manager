"""
ffmpeg-based thumbnail extraction for Video records.

Strategy: grab a frame ~1 second into the recording (or at the midpoint for
shorter clips), scale to 320px wide preserving aspect ratio, save as JPEG.
The first-second offset avoids the common black-fade-in opening frame.
"""
import logging
import os
import shutil
import subprocess

from django.core.files import File

logger = logging.getLogger(__name__)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def generate_thumbnail(video, *, force: bool = False) -> bool:
    """
    Extract a JPEG thumbnail for *video* using ffmpeg.

    Returns True on success, False otherwise. Skips if a thumbnail already
    exists unless force=True.
    """
    if not _ffmpeg_available():
        logger.warning("ffmpeg not installed — cannot generate thumbnail for %s", video.id)
        return False
    if not video.file:
        return False
    if video.thumbnail and not force:
        return True

    src = video.file.path
    if not os.path.isfile(src):
        return False

    # Pick an offset: 1 second in, or midpoint for very short clips.
    duration = video.duration_seconds or 0
    offset = 1.0 if duration >= 2 else max(duration / 2, 0.0)

    out_dir = os.path.join(os.path.dirname(src), "_thumbs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{video.id}.jpg")

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{offset:.3f}",
        "-i", src,
        "-frames:v", "1",
        "-vf", "scale=320:-2",   # 320 wide, height auto, force even
        "-q:v", "4",             # JPEG quality (2 best – 31 worst)
        out_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("ffmpeg thumbnail failed for %s: %s", video.id, e)
        return False

    if result.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) < 100:
        logger.warning("ffmpeg produced no thumbnail for %s: %s", video.id, result.stderr[:200])
        # Clean up an empty output file if one was created.
        if os.path.isfile(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        return False

    # Attach to the model via Django's FileField storage so the URL routing works.
    with open(out_path, "rb") as f:
        video.thumbnail.save(f"{video.id}.jpg", File(f), save=False)
    video.save(update_fields=["thumbnail"])

    # Remove the temp file (Django copied it into its own storage location).
    try:
        os.remove(out_path)
        # And clean up the _thumbs dir if it's now empty
        if not os.listdir(out_dir):
            os.rmdir(out_dir)
    except OSError:
        pass

    return True
