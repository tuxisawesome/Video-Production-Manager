from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0003_gallery_restructure"),
    ]

    operations = [
        migrations.AddField(
            model_name="video",
            name="health_status",
            field=models.CharField(
                choices=[
                    ("unknown", "Unknown"),
                    ("ok", "OK"),
                    ("audio_only", "Audio only — no video stream"),
                    ("corrupted", "Corrupted — container unreadable"),
                    ("empty", "Empty — no decodable streams"),
                ],
                default="unknown",
                help_text="Result of automated ffprobe health check.",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="video",
            name="health_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="video",
            name="health_detail",
            field=models.TextField(blank=True, help_text="ffprobe error or note."),
        ),
    ]
