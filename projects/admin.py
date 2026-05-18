from django.contrib import admin

from .models import Gallery, GalleryShare, Project, ProjectShare, ShareLink, Video, VideoComment


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at", "updated_at")
    list_filter = ("owner", "created_at")
    search_fields = ("name", "description", "owner__username")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-updated_at",)


@admin.register(Gallery)
class GalleryAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "created_at", "updated_at")
    list_filter = ("project", "created_at")
    search_fields = ("name", "description", "project__name")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-updated_at",)


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = (
        "filename_original",
        "gallery",
        "elo_rating",
        "comparison_count",
        "file_size_bytes",
        "created_at",
    )
    list_filter = ("gallery", "created_at")
    search_fields = ("filename_original", "gallery__name")
    readonly_fields = ("id", "created_at")
    ordering = ("-created_at",)


@admin.register(ProjectShare)
class ProjectShareAdmin(admin.ModelAdmin):
    list_display = ("project", "shared_with", "role", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("project__name", "shared_with__username")


@admin.register(GalleryShare)
class GalleryShareAdmin(admin.ModelAdmin):
    list_display = ("gallery", "shared_with", "role", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("gallery__name", "shared_with__username")


@admin.register(ShareLink)
class ShareLinkAdmin(admin.ModelAdmin):
    list_display = ("token", "access_type", "project", "gallery", "video", "created_by", "created_at")
    list_filter = ("access_type", "created_at")
    readonly_fields = ("token", "created_at")
