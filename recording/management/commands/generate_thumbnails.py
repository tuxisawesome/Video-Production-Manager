"""
Backfill thumbnails for videos that don't have one.

Usage:
    python manage.py generate_thumbnails             # missing thumbnails only
    python manage.py generate_thumbnails --force     # regenerate everything
    python manage.py generate_thumbnails --gallery <uuid>
"""
from django.core.management.base import BaseCommand

from projects.models import Video
from recording.thumbnails import generate_thumbnail


class Command(BaseCommand):
    help = "Generate thumbnails for videos that are missing one."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true",
            help="Regenerate thumbnails even for videos that already have one.",
        )
        parser.add_argument(
            "--gallery", type=str, default=None,
            help="Restrict to a single gallery UUID.",
        )

    def handle(self, *args, **options):
        qs = Video.objects.all()
        if not options["force"]:
            qs = qs.filter(thumbnail="")
        if options["gallery"]:
            qs = qs.filter(gallery_id=options["gallery"])

        # Don't bother generating thumbnails for known-bad recordings.
        qs = qs.exclude(health_status__in=[
            Video.HEALTH_CORRUPTED,
            Video.HEALTH_EMPTY,
            Video.HEALTH_AUDIO_ONLY,
        ])

        total = qs.count()
        if total == 0:
            self.stdout.write("Nothing to do.")
            return

        self.stdout.write(f"Generating thumbnails for {total} video(s)…")
        ok = 0
        failed = 0
        for i, video in enumerate(qs.iterator(), 1):
            success = generate_thumbnail(video, force=options["force"])
            if success:
                ok += 1
                self.stdout.write(self.style.SUCCESS(f"  [{i}/{total}] {video.id}  →  ok"))
            else:
                failed += 1
                self.stdout.write(self.style.WARNING(f"  [{i}/{total}] {video.id}  →  failed"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Generated {ok}, failed {failed}."))
