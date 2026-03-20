import os

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import SiteSettings

from .forms import ProjectForm, VideoUploadForm
from .models import Project, Video
from .utils import ZipStreamer


# ---------------------------------------------------------------------------
# Project views
# ---------------------------------------------------------------------------


@login_required
def project_list(request):
    """List all projects owned by the current user."""
    projects = Project.objects.filter(owner=request.user)
    form = ProjectForm()
    return render(request, "projects/project_list.html", {
        "projects": projects,
        "form": form,
    })


@login_required
def project_create(request):
    """Create a new project for the current user (POST only)."""
    if request.method != "POST":
        return redirect("projects:list")

    form = ProjectForm(request.POST)
    if form.is_valid():
        project = form.save(commit=False)
        project.owner = request.user
        project.save()
        messages.success(request, f'Project "{project.name}" created.')
        return redirect("projects:detail", pk=project.pk)

    # Re-render the list page with form errors.
    projects = Project.objects.filter(owner=request.user)
    return render(request, "projects/project_list.html", {
        "projects": projects,
        "form": form,
    })


@login_required
def project_detail(request, pk):
    """Show a single project with its videos sorted by ELO rating (desc)."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    videos = project.videos.order_by("-elo_rating")
    upload_form = VideoUploadForm()
    site_settings = SiteSettings.load()

    return render(request, "projects/project_detail.html", {
        "project": project,
        "videos": videos,
        "upload_form": upload_form,
        "site_settings": site_settings,
    })


@login_required
def project_delete(request, pk):
    """Delete a project and all its videos (POST only)."""
    if request.method != "POST":
        return redirect("projects:list")

    project = get_object_or_404(Project, pk=pk, owner=request.user)
    name = project.name

    # Delete associated video files from storage.
    for video in project.videos.all():
        if video.file:
            video.file.delete(save=False)
        if video.thumbnail:
            video.thumbnail.delete(save=False)

    project.delete()
    messages.success(request, f'Project "{name}" deleted.')
    return redirect("projects:list")


# ---------------------------------------------------------------------------
# Video views
# ---------------------------------------------------------------------------


@login_required
def video_upload(request, pk):
    """Handle manual video file upload for a project (POST only)."""
    if request.method != "POST":
        return redirect("projects:detail", pk=pk)

    project = get_object_or_404(Project, pk=pk, owner=request.user)

    # Check recording limit from SiteSettings.
    site_settings = SiteSettings.load()
    max_recordings = site_settings.max_recordings_per_project
    if max_recordings > 0 and project.videos.count() >= max_recordings:
        messages.error(
            request,
            f"This project has reached the maximum of {max_recordings} recordings.",
        )
        return redirect("projects:detail", pk=pk)

    form = VideoUploadForm(request.POST, request.FILES)
    if form.is_valid():
        uploaded_file = form.cleaned_data["file"]
        video = Video(
            project=project,
            file=uploaded_file,
            filename_original=uploaded_file.name,
            file_size_bytes=uploaded_file.size,
        )
        video.save()
        messages.success(request, f'Video "{uploaded_file.name}" uploaded.')
        return redirect("projects:detail", pk=pk)

    # Re-render detail page with form errors.
    videos = project.videos.order_by("-elo_rating")
    return render(request, "projects/project_detail.html", {
        "project": project,
        "videos": videos,
        "upload_form": form,
        "site_settings": site_settings,
    })


@login_required
def video_delete(request, pk, video_id):
    """Delete a single video and its file from storage (POST only)."""
    if request.method != "POST":
        return redirect("projects:detail", pk=pk)

    project = get_object_or_404(Project, pk=pk, owner=request.user)
    video = get_object_or_404(Video, pk=video_id, project=project)

    # Remove physical files.
    if video.file:
        video.file.delete(save=False)
    if video.thumbnail:
        video.thumbnail.delete(save=False)

    video.delete()
    messages.success(request, "Video deleted.")
    return redirect("projects:detail", pk=pk)


@login_required
def video_download(request, pk, video_id):
    """
    Serve a video file for download.

    In DEBUG mode, use Django's FileResponse directly.
    In production, use X-Accel-Redirect to let nginx serve the file.
    """
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    video = get_object_or_404(Video, pk=video_id, project=project)

    if not video.file:
        raise Http404("Video file not found.")

    filename = video.filename_original or f"{video.id}.webm"

    if settings.DEBUG:
        file_path = video.file.path
        if not os.path.isfile(file_path):
            raise Http404("Video file not found on disk.")
        response = FileResponse(
            open(file_path, "rb"),
            content_type="video/webm",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    else:
        # Production: let nginx handle the file serving.
        response = HttpResponse()
        response["Content-Type"] = "video/webm"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["X-Accel-Redirect"] = f"/protected-media/{video.file.name}"
        return response


@login_required
def download_all(request, pk):
    """
    Stream all videos in a project as a zip archive.

    Uses ZipStreamer to yield chunks without building the full zip in memory.
    """
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    videos = project.videos.all()

    if not videos.exists():
        messages.info(request, "No videos to download.")
        return redirect("projects:detail", pk=pk)

    # Build the list of (archive_name, file_path) entries.
    files = []
    for video in videos:
        if video.file:
            file_path = video.file.path
            if os.path.isfile(file_path):
                arcname = video.filename_original or f"{video.id}.webm"
                files.append((arcname, file_path))

    if not files:
        messages.info(request, "No video files found on disk.")
        return redirect("projects:detail", pk=pk)

    streamer = ZipStreamer()
    response = StreamingHttpResponse(
        streamer.stream(files),
        content_type="application/zip",
    )
    zip_filename = f"{project.name}.zip".replace(" ", "_")
    response["Content-Disposition"] = f'attachment; filename="{zip_filename}"'
    return response
