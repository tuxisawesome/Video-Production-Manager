import json
import os

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.models import SiteSettings

from .forms import ProjectForm, VideoUploadForm
from .models import Project, ProjectShare, ShareLink, Video, VideoComment
from .utils import ZipStreamer

User = get_user_model()

# Session key used to mark a share link as unlocked.
_SHARE_SESSION_KEY = "unlocked_share_{token}"


def _session_key(token):
    return f"unlocked_share_{token}"


def _get_accessible_project(user, pk):
    """Return (project, is_owner). Raises Http404 if user has no access."""
    project = get_object_or_404(Project, pk=pk)
    if project.owner == user:
        return project, True
    if project.shares.filter(shared_with=user).exists():
        return project, False
    raise Http404


# ---------------------------------------------------------------------------
# Project views
# ---------------------------------------------------------------------------


@login_required
def project_list(request):
    """List projects owned by or shared with the current user."""
    owned = Project.objects.filter(owner=request.user)
    shared_ids = ProjectShare.objects.filter(
        shared_with=request.user
    ).values_list("project_id", flat=True)
    shared = Project.objects.filter(id__in=shared_ids).exclude(owner=request.user)
    form = ProjectForm()
    return render(request, "projects/project_list.html", {
        "projects": owned,
        "shared_projects": shared,
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
    project, is_owner = _get_accessible_project(request.user, pk)
    videos = project.videos.order_by("-elo_rating")
    upload_form = VideoUploadForm()
    site_settings = SiteSettings.load()
    shares = project.shares.select_related("shared_with").all() if is_owner else []
    share_links = project.share_links.all() if is_owner else []

    return render(request, "projects/project_detail.html", {
        "project": project,
        "is_owner": is_owner,
        "videos": videos,
        "upload_form": upload_form,
        "site_settings": site_settings,
        "shares": shares,
        "share_links": share_links,
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

    project, is_owner = _get_accessible_project(request.user, pk)
    if not is_owner:
        messages.error(request, "Only the project owner can upload videos.")
        return redirect("projects:detail", pk=pk)

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
def video_stream(request, pk, video_id):
    """
    Serve a video file for inline playback (no Content-Disposition: attachment).

    In DEBUG mode, use Django's FileResponse directly.
    In production, use X-Accel-Redirect to let nginx serve the file.
    """
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    video = get_object_or_404(Video, pk=video_id, project=project)

    if not video.file:
        raise Http404("Video file not found.")

    if settings.DEBUG:
        file_path = video.file.path
        if not os.path.isfile(file_path):
            raise Http404("Video file not found on disk.")
        return FileResponse(
            open(file_path, "rb"),
            content_type="video/webm",
        )
    else:
        response = HttpResponse()
        response["Content-Type"] = "video/webm"
        response["X-Accel-Redirect"] = f"/protected-media/{video.file.name}"
        return response


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


# ---------------------------------------------------------------------------
# Project sharing
# ---------------------------------------------------------------------------


@login_required
@require_POST
def add_share_view(request, pk):
    """Owner adds a user to the project share list."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    username = request.POST.get("username", "").strip()

    if not username:
        messages.error(request, "Enter a username to share with.")
        return redirect("projects:detail", pk=pk)

    try:
        target = User.objects.get(username=username)
    except User.DoesNotExist:
        messages.error(request, f'User "{username}" not found.')
        return redirect("projects:detail", pk=pk)

    if target == request.user:
        messages.error(request, "You cannot share a project with yourself.")
        return redirect("projects:detail", pk=pk)

    can_comment = request.POST.get("can_comment") == "on"
    share, created = ProjectShare.objects.get_or_create(
        project=project,
        shared_with=target,
        defaults={"can_comment": can_comment},
    )
    if not created:
        share.can_comment = can_comment
        share.save()
        messages.success(request, f'Updated share for "{username}".')
    else:
        messages.success(request, f'Project shared with "{username}".')
    return redirect("projects:detail", pk=pk)


@login_required
@require_POST
def remove_share_view(request, pk, share_id):
    """Owner removes a user from the project share list."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    share = get_object_or_404(ProjectShare, pk=share_id, project=project)
    name = share.shared_with.username
    share.delete()
    messages.success(request, f'Removed share for "{name}".')
    return redirect("projects:detail", pk=pk)


# ---------------------------------------------------------------------------
# Video comments
# ---------------------------------------------------------------------------


@login_required
def comment_list_view(request, pk, video_id):
    """Return comments for a video as JSON."""
    project, _ = _get_accessible_project(request.user, pk)
    video = get_object_or_404(Video, pk=video_id, project=project)
    comments = [
        {
            "id": c.id,
            "author": c.author.username,
            "text": c.text,
            "timestamp_seconds": c.timestamp_seconds,
            "created_at": c.created_at.isoformat(),
            "is_own": c.author == request.user,
        }
        for c in video.comments.select_related("author").all()
    ]
    return JsonResponse({"comments": comments})


@login_required
@require_POST
def comment_create_view(request, pk, video_id):
    """Create a comment on a video."""
    project, is_owner = _get_accessible_project(request.user, pk)
    # Non-owners need can_comment permission.
    if not is_owner:
        share = get_object_or_404(
            ProjectShare, project=project, shared_with=request.user
        )
        if not share.can_comment:
            return JsonResponse({"error": "You do not have permission to comment."}, status=403)

    video = get_object_or_404(Video, pk=video_id, project=project)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    text = data.get("text", "").strip()
    if not text:
        return JsonResponse({"error": "Comment text is required."}, status=400)

    ts = data.get("timestamp_seconds")
    if ts is not None:
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            ts = None

    comment = VideoComment.objects.create(
        video=video,
        author=request.user,
        text=text,
        timestamp_seconds=ts,
    )
    return JsonResponse({
        "id": comment.id,
        "author": comment.author.username,
        "text": comment.text,
        "timestamp_seconds": comment.timestamp_seconds,
        "created_at": comment.created_at.isoformat(),
        "is_own": True,
    }, status=201)


@login_required
@require_POST
def comment_delete_view(request, pk, video_id, comment_id):
    """Delete a comment (owner of project or comment author)."""
    project, is_owner = _get_accessible_project(request.user, pk)
    video = get_object_or_404(Video, pk=video_id, project=project)
    comment = get_object_or_404(VideoComment, pk=comment_id, video=video)

    if comment.author != request.user and not is_owner:
        return JsonResponse({"error": "Permission denied."}, status=403)

    comment.delete()
    return JsonResponse({"deleted": True})


# ---------------------------------------------------------------------------
# Share links
# ---------------------------------------------------------------------------


@login_required
@require_POST
def share_link_create_view(request, pk):
    """Create a share link for a project (rank) or a specific video (view)."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    link_type = request.POST.get("link_type", "rank")
    raw_password = request.POST.get("password", "").strip()
    video_id = request.POST.get("video_id", "").strip()

    link = ShareLink(link_type=link_type, created_by=request.user)

    if link_type == ShareLink.RANK:
        link.project = project
    elif link_type == ShareLink.VIEW:
        if not video_id:
            messages.error(request, "No video specified for view link.")
            return redirect("projects:detail", pk=pk)
        link.video = get_object_or_404(Video, pk=video_id, project=project)
    else:
        messages.error(request, "Invalid link type.")
        return redirect("projects:detail", pk=pk)

    link.set_password(raw_password)
    link.save()
    messages.success(request, "Share link created.")
    return redirect("projects:detail", pk=pk)


@login_required
@require_POST
def share_link_delete_view(request, pk, token):
    """Delete a share link owned by the user."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    link = get_object_or_404(ShareLink, token=token)
    # Ensure the link belongs to this project or one of its videos.
    if link.project != project and (link.video is None or link.video.project != project):
        raise Http404
    link.delete()
    messages.success(request, "Share link deleted.")
    return redirect("projects:detail", pk=pk)


# ---------------------------------------------------------------------------
# Public share link gate + viewers
# ---------------------------------------------------------------------------


def share_gate_view(request, token):
    """Password entry page for a share link."""
    link = get_object_or_404(ShareLink, token=token)
    if link.is_expired:
        return render(request, "projects/share_expired.html", {}, status=410)

    session_key = _session_key(token)

    if request.method == "POST":
        entered = request.POST.get("password", "")
        if link.check_password(entered):
            request.session[session_key] = True
            return redirect(_share_destination(link, token))
        return render(request, "projects/share_gate.html", {
            "error": "Incorrect password.",
            "token": token,
        })

    # No password set → unlock immediately.
    if not link.has_password:
        request.session[session_key] = True
        return redirect(_share_destination(link, token))

    # Already unlocked in this session.
    if request.session.get(session_key):
        return redirect(_share_destination(link, token))

    return render(request, "projects/share_gate.html", {"token": token})


def _share_destination(link, token):
    if link.link_type == ShareLink.RANK:
        return f"/share/{token}/rank/"
    return f"/share/{token}/video/"


def _get_unlocked_link(request, token):
    """Return the ShareLink if the session has it unlocked, else None."""
    link = get_object_or_404(ShareLink, token=token)
    if link.is_expired:
        return None
    if not link.has_password or request.session.get(_session_key(token)):
        return link
    return None


def public_video_view(request, token):
    """Public video viewer page."""
    link = _get_unlocked_link(request, token)
    if link is None:
        return redirect(f"/share/{token}/")
    if link.link_type != ShareLink.VIEW or link.video is None:
        raise Http404
    return render(request, "projects/public_video.html", {"link": link, "video": link.video})


def public_video_stream(request, token):
    """Serve the video file for a public share link."""
    link = _get_unlocked_link(request, token)
    if link is None:
        raise Http404
    if link.link_type != ShareLink.VIEW or link.video is None:
        raise Http404
    video = link.video
    if not video.file:
        raise Http404
    if settings.DEBUG:
        file_path = video.file.path
        if not os.path.isfile(file_path):
            raise Http404
        return FileResponse(open(file_path, "rb"), content_type="video/webm")
    response = HttpResponse()
    response["Content-Type"] = "video/webm"
    response["X-Accel-Redirect"] = f"/protected-media/{video.file.name}"
    return response


def public_rank_view(request, token):
    """Public ranking page."""
    link = _get_unlocked_link(request, token)
    if link is None:
        return redirect(f"/share/{token}/")
    if link.link_type != ShareLink.RANK or link.project is None:
        raise Http404
    from recording.models import Comparison
    from recording.ranking import get_ranking_progress
    progress = get_ranking_progress(link.project_id)
    return render(request, "projects/public_rank.html", {
        "link": link,
        "project": link.project,
        "progress": progress,
        "token": token,
    })


@require_GET
def public_next_pair_view(request, token):
    """Public API: get next pair to compare."""
    link = _get_unlocked_link(request, token)
    if link is None:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    if link.link_type != ShareLink.RANK or link.project is None:
        raise Http404
    from django.urls import reverse as _reverse
    from recording.ranking import get_ranking_progress, select_next_pair
    progress = get_ranking_progress(link.project_id)
    pair = select_next_pair(link.project_id)
    if pair is None:
        return JsonResponse({"complete": True, "progress": progress})
    video_a, video_b = pair
    return JsonResponse({
        "complete": False,
        "video_left": {
            "id": str(video_a.id),
            "url": f"/share/{token}/video-file/{video_a.id}/",
            "name": video_a.filename_original or str(video_a.id),
            "elo": round(video_a.elo_rating, 1),
        },
        "video_right": {
            "id": str(video_b.id),
            "url": f"/share/{token}/video-file/{video_b.id}/",
            "name": video_b.filename_original or str(video_b.id),
            "elo": round(video_b.elo_rating, 1),
        },
        "progress": progress,
    })


@require_POST
def public_submit_comparison_view(request, token):
    """Public API: submit a comparison result."""
    link = _get_unlocked_link(request, token)
    if link is None:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    if link.link_type != ShareLink.RANK or link.project is None:
        raise Http404

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    video_left_id = data.get("video_left")
    video_right_id = data.get("video_right")
    result = data.get("result")
    if result not in ("left", "right", "equal"):
        return JsonResponse({"error": "Invalid result."}, status=400)

    from recording.models import Comparison
    from recording.ranking import update_elo
    video_left = get_object_or_404(Video, pk=video_left_id, project=link.project)
    video_right = get_object_or_404(Video, pk=video_right_id, project=link.project)
    Comparison.objects.create(
        project=link.project,
        video_left=video_left,
        video_right=video_right,
        result=result,
        user=None,  # anonymous via share link
    )
    update_elo(video_left, video_right, result)
    return JsonResponse({"success": True})


def public_rank_video_file(request, token, video_id):
    """Serve a video file for the public ranking page."""
    link = _get_unlocked_link(request, token)
    if link is None:
        raise Http404
    if link.link_type != ShareLink.RANK or link.project is None:
        raise Http404
    video = get_object_or_404(Video, pk=video_id, project=link.project)
    if not video.file:
        raise Http404
    if settings.DEBUG:
        file_path = video.file.path
        if not os.path.isfile(file_path):
            raise Http404
        return FileResponse(open(file_path, "rb"), content_type="video/webm")
    response = HttpResponse()
    response["Content-Type"] = "video/webm"
    response["X-Accel-Redirect"] = f"/protected-media/{video.file.name}"
    return response
