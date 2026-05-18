"""
Migration: Gallery restructure

Project → Gallery → Video hierarchy.

Steps:
1. Create Gallery model
2. Add Video.gallery (nullable FK to Gallery)
3. Add ShareLink.gallery (nullable FK to Gallery)
4. Rename ShareLink.link_type → access_type, update max_length to 15
5. Remove ProjectShare.can_comment, add ProjectShare.role CharField
6. Create GalleryShare model
7. Data migration: create one Gallery per Project, assign videos to galleries,
   map old link_type values to new access_type values
8. Make Video.gallery non-nullable
9. Remove Video.project
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def forward_data(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    Gallery = apps.get_model("projects", "Gallery")
    Video = apps.get_model("projects", "Video")
    ShareLink = apps.get_model("projects", "ShareLink")

    # Create one default gallery per project, then assign all videos.
    for project in Project.objects.all():
        gallery = Gallery.objects.create(
            id=uuid.uuid4(),
            project=project,
            name="Main Gallery",
            description="",
        )
        Video.objects.filter(project_id=project.pk).update(gallery=gallery)

    # Map old link_type values to new access_type values.
    # Old values: 'rank', 'view', 'both'
    for link in ShareLink.objects.all():
        old_type = link.link_type
        if old_type == "rank":
            link.access_type = "rank"
        elif old_type == "both":
            link.access_type = "rank"
        else:
            link.access_type = "view"
        link.save(update_fields=["access_type"])

    # Map old ProjectShare.can_comment to role.
    ProjectShare = apps.get_model("projects", "ProjectShare")
    for share in ProjectShare.objects.all():
        if share.can_comment:
            share.role = "commentator"
        else:
            share.role = "view"
        share.save(update_fields=["role"])


def backward_data(apps, schema_editor):
    # No-op: we can't fully reverse the data migration.
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("projects", "0002_sharing_comments_sharelinks"),
    ]

    operations = [
        # 1. Create Gallery model
        migrations.CreateModel(
            name="Gallery",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="galleries",
                        to="projects.project",
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at"],
            },
        ),
        # 2. Add Video.gallery (nullable for now)
        migrations.AddField(
            model_name="video",
            name="gallery",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="videos",
                to="projects.gallery",
            ),
        ),
        # 3. Add ShareLink.gallery (nullable FK)
        migrations.AddField(
            model_name="sharelink",
            name="gallery",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="share_links",
                to="projects.gallery",
            ),
        ),
        # 4a. Add new access_type field (nullable first so existing rows are fine)
        migrations.AddField(
            model_name="sharelink",
            name="access_type",
            field=models.CharField(
                max_length=15,
                choices=[
                    ("view", "View only"),
                    ("rank", "View + Rank"),
                    ("commentator", "Commentator (View, Rank, Comment, Download)"),
                ],
                default="view",
            ),
        ),
        # 4b. Add role field to ProjectShare (nullable first so existing rows are fine)
        migrations.AddField(
            model_name="projectshare",
            name="role",
            field=models.CharField(
                max_length=15,
                choices=[
                    ("view", "View only"),
                    ("rank", "View + Rank"),
                    ("commentator", "Commentator"),
                ],
                default="view",
            ),
        ),
        # 5. Create GalleryShare model
        migrations.CreateModel(
            name="GalleryShare",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        max_length=15,
                        choices=[
                            ("view", "View only"),
                            ("rank", "View + Rank"),
                            ("commentator", "Commentator"),
                        ],
                        default="view",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "gallery",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shares",
                        to="projects.gallery",
                    ),
                ),
                (
                    "shared_with",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shared_galleries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
                "unique_together": {("gallery", "shared_with")},
            },
        ),
        # 6. Data migration
        migrations.RunPython(forward_data, backward_data, atomic=False),
        # 7. Make Video.gallery non-nullable
        migrations.AlterField(
            model_name="video",
            name="gallery",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="videos",
                to="projects.gallery",
            ),
        ),
        # 8. Remove Video.project
        migrations.RemoveField(
            model_name="video",
            name="project",
        ),
        # 9. Remove ProjectShare.can_comment
        migrations.RemoveField(
            model_name="projectshare",
            name="can_comment",
        ),
        # 10. Remove ShareLink.link_type
        migrations.RemoveField(
            model_name="sharelink",
            name="link_type",
        ),
    ]
