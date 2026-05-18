"""
Migration: Replace project FK with gallery FK on RecordingSession and Comparison.
"""

import django.db.models.deletion
from django.db import migrations, models


def forward_data(apps, schema_editor):
    Gallery = apps.get_model("projects", "Gallery")
    RecordingSession = apps.get_model("recording", "RecordingSession")
    Comparison = apps.get_model("recording", "Comparison")

    # For each RecordingSession, find the first gallery for its project.
    for session in RecordingSession.objects.all():
        gallery = (
            Gallery.objects.filter(project_id=session.project_id)
            .order_by("created_at")
            .first()
        )
        if gallery:
            session.gallery = gallery
            session.save(update_fields=["gallery"])

    # Same for Comparison.
    for comparison in Comparison.objects.all():
        gallery = (
            Gallery.objects.filter(project_id=comparison.project_id)
            .order_by("created_at")
            .first()
        )
        if gallery:
            comparison.gallery = gallery
            comparison.save(update_fields=["gallery"])


def backward_data(apps, schema_editor):
    # No-op
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("recording", "0002_comparison_user_nullable"),
        ("projects", "0003_gallery_restructure"),
    ]

    operations = [
        # 1. Add gallery FK to RecordingSession (nullable first)
        migrations.AddField(
            model_name="recordingsession",
            name="gallery",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="recording_sessions",
                to="projects.gallery",
            ),
        ),
        # 2. Add gallery FK to Comparison (nullable first)
        migrations.AddField(
            model_name="comparison",
            name="gallery",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="comparisons",
                to="projects.gallery",
            ),
        ),
        # 3. Data migration
        migrations.RunPython(forward_data, backward_data, atomic=False),
        # 4. Make RecordingSession.gallery non-nullable
        migrations.AlterField(
            model_name="recordingsession",
            name="gallery",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="recording_sessions",
                to="projects.gallery",
            ),
        ),
        # 5. Remove RecordingSession.project
        migrations.RemoveField(
            model_name="recordingsession",
            name="project",
        ),
        # 6. Make Comparison.gallery non-nullable
        migrations.AlterField(
            model_name="comparison",
            name="gallery",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="comparisons",
                to="projects.gallery",
            ),
        ),
        # 7. Remove Comparison.project
        migrations.RemoveField(
            model_name="comparison",
            name="project",
        ),
    ]
