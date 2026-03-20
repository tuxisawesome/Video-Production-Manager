from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import SiteSettings, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin configuration for the custom User model."""

    list_display = [
        "username",
        "email",
        "max_recording_seconds",
        "is_staff",
        "is_active",
        "created_by",
        "date_joined",
    ]
    list_filter = ["is_staff", "is_active", "date_joined"]
    search_fields = ["username", "email"]

    # Add the custom fields to the existing UserAdmin fieldsets.
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "Recording Settings",
            {
                "fields": ("max_recording_seconds", "created_by"),
            },
        ),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (
            "Recording Settings",
            {
                "fields": ("max_recording_seconds",),
            },
        ),
    )


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Admin configuration for the SiteSettings singleton."""

    list_display = ["__str__", "max_recordings_per_project"]

    def has_add_permission(self, request):
        # Prevent creating more than one instance.
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Prevent deletion of the singleton.
        return False
