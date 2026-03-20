from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("", views.project_list, name="list"),
    path("create/", views.project_create, name="create"),
    path("<uuid:pk>/", views.project_detail, name="detail"),
    path("<uuid:pk>/delete/", views.project_delete, name="delete"),
    path("<uuid:pk>/upload/", views.video_upload, name="video_upload"),
    path(
        "<uuid:pk>/videos/<uuid:video_id>/delete/",
        views.video_delete,
        name="video_delete",
    ),
    path(
        "<uuid:pk>/videos/<uuid:video_id>/stream/",
        views.video_stream,
        name="video_stream",
    ),
    path(
        "<uuid:pk>/videos/<uuid:video_id>/download/",
        views.video_download,
        name="video_download",
    ),
    path("<uuid:pk>/download-all/", views.download_all, name="download_all"),
]
