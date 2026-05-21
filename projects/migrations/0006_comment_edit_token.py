from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0005_guest_comments"),
    ]

    operations = [
        migrations.AddField(
            model_name="videocomment",
            name="edit_token",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="videocomment",
            name="edited_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
