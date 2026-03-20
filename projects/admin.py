from django.contrib import admin

from .models import Project, Video


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at", "updated_at")
    list_filter = ("owner", "created_at")
    search_fields = ("name", "description", "owner__username")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-updated_at",)


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = (
        "filename_original",
        "project",
        "elo_rating",
        "comparison_count",
        "file_size_bytes",
        "created_at",
    )
    list_filter = ("project", "created_at")
    search_fields = ("filename_original", "project__name")
    readonly_fields = ("id", "created_at")
    ordering = ("-created_at",)
