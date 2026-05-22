from django.db import migrations, models


def escape_to_enter(apps, schema_editor):
    """
    Anyone still on the old 'Escape' default gets bumped to 'Enter'. Users
    who actively chose something other than 'Escape' (Space, KeyD, etc.) are
    left alone.
    """
    KeybindPreference = apps.get_model("recording", "KeybindPreference")
    KeybindPreference.objects.filter(discard_key="Escape").update(discard_key="Enter")


def enter_to_escape(apps, schema_editor):
    """Reverse — only meant for `migrate recording 0003`."""
    KeybindPreference = apps.get_model("recording", "KeybindPreference")
    KeybindPreference.objects.filter(discard_key="Enter").update(discard_key="Escape")


class Migration(migrations.Migration):

    dependencies = [
        ("recording", "0003_gallery_fk"),
    ]

    operations = [
        migrations.AlterField(
            model_name="keybindpreference",
            name="discard_key",
            field=models.CharField(default="Enter", max_length=50),
        ),
        migrations.RunPython(escape_to_enter, enter_to_escape),
    ]
