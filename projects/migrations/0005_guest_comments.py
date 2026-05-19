import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0004_video_health_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="videocomment",
            name="author",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="video_comments",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="videocomment",
            name="guest_name",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="videocomment",
            name="share_link_token",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
