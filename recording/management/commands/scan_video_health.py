"""
Run ffprobe on every Video and update its health_status.

Usage:
    python manage.py scan_video_health                 # all videos
    python manage.py scan_video_health --unknown-only  # only unscanned
    python manage.py scan_video_health --gallery <id>  # one gallery
"""
from django.core.management.base import BaseCommand

from projects.models import Video
from recording.health import update_video_health


class Command(BaseCommand):
    help = "Scan all Video files with ffprobe and update their health_status."

    def add_arguments(self, parser):
        parser.add_argument(
            "--unknown-only", action="store_true",
            help="Only scan videos whose health_status is still 'unknown'.",
        )
        parser.add_argument(
            "--gallery", type=str, default=None,
            help="Restrict to a single gallery UUID.",
        )

    def handle(self, *args, **options):
        qs = Video.objects.all()
        if options["unknown_only"]:
            qs = qs.filter(health_status=Video.HEALTH_UNKNOWN)
        if options["gallery"]:
            qs = qs.filter(gallery_id=options["gallery"])

        total = qs.count()
        if total == 0:
            self.stdout.write("Nothing to scan.")
            return

        self.stdout.write(f"Scanning {total} video(s)…")
        counts = {}
        for i, video in enumerate(qs.iterator(), 1):
            status = update_video_health(video)
            counts[status] = counts.get(status, 0) + 1

            style = self.style.SUCCESS if status == Video.HEALTH_OK else self.style.WARNING
            self.stdout.write(style(
                f"  [{i}/{total}] {video.id}  →  {status}"
                + (f"  ({video.health_detail})" if video.health_detail else "")
            ))

        self.stdout.write("")
        self.stdout.write("Summary:")
        for status, n in sorted(counts.items()):
            self.stdout.write(f"  {status:12s} {n}")
