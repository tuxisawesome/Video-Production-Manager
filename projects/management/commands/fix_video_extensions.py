"""
Inspect existing Video files, detect the real container format from the
file header (magic bytes), and rename to the correct extension. Helps
recover recordings that were saved as .webm but actually contain MP4
data (iOS Safari MediaRecorder).

Usage:
    python manage.py fix_video_extensions           # dry run, reports only
    python manage.py fix_video_extensions --apply   # rename + update DB
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from projects.models import Video


def detect_container(path):
    """Return 'mp4', 'webm', or None based on the first ~16 bytes."""
    try:
        with open(path, 'rb') as f:
            head = f.read(16)
    except OSError:
        return None
    if len(head) < 8:
        return None
    # ISO BMFF / MP4: 4-byte size, then 'ftyp' at offset 4
    if head[4:8] == b'ftyp':
        return 'mp4'
    # WebM / Matroska: EBML header starts with 0x1A 0x45 0xDF 0xA3
    if head[:4] == b'\x1a\x45\xdf\xa3':
        return 'webm'
    return None


class Command(BaseCommand):
    help = 'Detect and fix mismatched video file extensions (e.g. MP4 saved as .webm).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually rename files and update the database (default: dry run).',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        fixed = 0
        scanned = 0
        missing = 0
        unknown = 0

        for video in Video.objects.all():
            scanned += 1
            if not video.file:
                continue
            file_path = os.path.join(settings.MEDIA_ROOT, video.file.name)
            if not os.path.isfile(file_path):
                missing += 1
                self.stdout.write(f'  MISSING  {video.id}  {video.file.name}')
                continue

            real = detect_container(file_path)
            if real is None:
                unknown += 1
                self.stdout.write(f'  UNKNOWN  {video.id}  {video.file.name}')
                continue

            current_ext = os.path.splitext(video.file.name)[1].lower().lstrip('.')
            if current_ext == real:
                continue  # already correct

            new_rel = os.path.splitext(video.file.name)[0] + f'.{real}'
            new_abs = os.path.join(settings.MEDIA_ROOT, new_rel)
            new_filename = (video.filename_original or '').rsplit('.', 1)[0] + f'.{real}'

            self.stdout.write(self.style.WARNING(
                f'  MISMATCH  {video.id}  {current_ext} → {real}  ({video.file.name})'
            ))

            if apply:
                os.rename(file_path, new_abs)
                video.file = new_rel
                video.filename_original = new_filename
                video.save(update_fields=['file', 'filename_original'])
                fixed += 1

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Scanned {scanned}, fixed {fixed}, missing {missing}, unknown {unknown}.'
        ))
        if not apply:
            self.stdout.write(self.style.NOTICE('Dry run — re-run with --apply to commit changes.'))
