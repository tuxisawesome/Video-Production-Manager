from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    # Projects
    path("", views.project_list, name="list"),
    path("create/", views.project_create, name="create"),
    path("<uuid:pk>/", views.project_detail, name="detail"),
    path("<uuid:pk>/delete/", views.project_delete, name="delete"),

    # Project sharing (authenticated)
    path("<uuid:pk>/share/add/", views.add_share_view, name="share_add"),
    path("<uuid:pk>/share/<int:share_id>/remove/", views.remove_share_view, name="share_remove"),
    path("<uuid:pk>/share-links/create/", views.share_link_create_view, name="share_link_create"),
    path("<uuid:pk>/share-links/<uuid:token>/delete/", views.share_link_delete_view, name="share_link_delete"),

    # Videos (authenticated)
    path("<uuid:pk>/upload/", views.video_upload, name="video_upload"),
    path("<uuid:pk>/videos/<uuid:video_id>/delete/", views.video_delete, name="video_delete"),
    path("<uuid:pk>/videos/<uuid:video_id>/stream/", views.video_stream, name="video_stream"),
    path("<uuid:pk>/videos/<uuid:video_id>/download/", views.video_download, name="video_download"),
    path("<uuid:pk>/download-all/", views.download_all, name="download_all"),

    # Video comments (authenticated)
    path("<uuid:pk>/videos/<uuid:video_id>/comments/", views.comment_list_view, name="comment_list"),
    path("<uuid:pk>/videos/<uuid:video_id>/comments/create/", views.comment_create_view, name="comment_create"),
    path("<uuid:pk>/videos/<uuid:video_id>/comments/<int:comment_id>/delete/", views.comment_delete_view, name="comment_delete"),

    # Public share link routes (no login required)
    path("share/<uuid:token>/", views.share_gate_view, name="share_gate"),
    path("share/<uuid:token>/gallery/", views.public_gallery_view, name="public_gallery"),
    path("share/<uuid:token>/gallery/video/<uuid:video_id>/", views.public_gallery_video_stream, name="public_gallery_video_stream"),
    path("share/<uuid:token>/rank/", views.public_rank_view, name="public_rank"),
    path("share/<uuid:token>/rank/next-pair/", views.public_next_pair_view, name="public_next_pair"),
    path("share/<uuid:token>/rank/submit/", views.public_submit_comparison_view, name="public_submit"),
    path("share/<uuid:token>/video-file/<uuid:video_id>/", views.public_rank_video_file, name="public_rank_video_file"),
]
