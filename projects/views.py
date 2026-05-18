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
from django.urls import reverse as _url_reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.models import SiteSettings

from .forms import ProjectForm, VideoUploadForm
from .models import Gallery, GalleryShare, Project, ProjectShare, ShareLink, Video, VideoComment
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


def _get_accessible_gallery(user, project_pk, gallery_pk):
    """
    Return (gallery, role) where role is 'owner'|'commentator'|'rank'|'view'.
    Raises Http404 if the gallery does not exist or the user has no access.
    """
    gallery = get_object_or_404(Gallery, pk=gallery_pk, project__pk=project_pk)
    if gallery.project.owner == user:
        return gallery, 'owner'
    # Project share applies to all galleries in the project.
    try:
        ps = ProjectShare.objects.get(project_id=gallery.project_id, shared_with=user)
        return gallery, ps.role
    except ProjectShare.DoesNotExist:
        pass
    # Gallery-specific share.
    try:
        gs = GalleryShare.objects.get(gallery=gallery, shared_with=user)
        return gallery, gs.role
    except GalleryShare.DoesNotExist:
        pass
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
    """Show a single project with its galleries."""
    project, is_owner = _get_accessible_project(request.user, pk)
    galleries = project.galleries.all()
    shares = project.shares.select_related("shared_with").all() if is_owner else []

    return render(request, "projects/project_detail.html", {
        "project": project,
        "is_owner": is_owner,
        "galleries": galleries,
        "shares": shares,
    })


@login_required
def project_delete(request, pk):
    """Delete a project and all its galleries/videos (POST only)."""
    if request.method != "POST":
        return redirect("projects:list")

    project = get_object_or_404(Project, pk=pk, owner=request.user)
    name = project.name

    # Delete associated video files from storage.
    for gallery in project.galleries.all():
        for video in gallery.videos.all():
            if video.file:
                video.file.delete(save=False)
            if video.thumbnail:
                video.thumbnail.delete(save=False)

    project.delete()
    messages.success(request, f'Project "{name}" deleted.')
    return redirect("projects:list")


# ---------------------------------------------------------------------------
# Gallery views
# ---------------------------------------------------------------------------


@login_required
@require_POST
def gallery_create(request, pk):
    """Create a new gallery in a project (POST only, owner only)."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    name = request.POST.get("name", "").strip()
    description = request.POST.get("description", "").strip()

    if not name:
        messages.error(request, "Gallery name is required.")
        return redirect("projects:detail", pk=pk)

    gallery = Gallery.objects.create(
        project=project,
        name=name,
        description=description,
    )
    messages.success(request, f'Gallery "{gallery.name}" created.')
    return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery.pk)


@login_required
@require_POST
def gallery_delete(request, pk, gallery_pk):
    """Delete a gallery and all its videos (POST only, owner only)."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    gallery = get_object_or_404(Gallery, pk=gallery_pk, project=project)

    for video in gallery.videos.all():
        if video.file:
            video.file.delete(save=False)
        if video.thumbnail:
            video.thumbnail.delete(save=False)

    gallery.delete()
    messages.success(request, "Gallery deleted.")
    return redirect("projects:detail", pk=pk)


@login_required
def gallery_detail(request, pk, gallery_pk):
    """Show a single gallery with its videos sorted by ELO rating (desc)."""
    gallery, role = _get_accessible_gallery(request.user, pk, gallery_pk)
    is_owner = role == 'owner'
    project = gallery.project
    videos = gallery.videos.order_by("-elo_rating")
    upload_form = VideoUploadForm()
    site_settings = SiteSettings.load()
    shares = project.shares.select_related("shared_with").all() if is_owner else []
    gallery_shares = gallery.shares.select_related("shared_with").all() if is_owner else []
    share_links = gallery.share_links.all() if is_owner else []

    return render(request, "projects/gallery_detail.html", {
        "gallery": gallery,
        "project": project,
        "role": role,
        "is_owner": is_owner,
        "videos": videos,
        "upload_form": upload_form,
        "site_settings": site_settings,
        "shares": shares,
        "gallery_shares": gallery_shares,
        "share_links": share_links,
    })


# ---------------------------------------------------------------------------
# Video views
# ---------------------------------------------------------------------------


@login_required
def video_upload(request, pk, gallery_pk):
    """Handle manual video file upload for a gallery (POST only)."""
    if request.method != "POST":
        return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)

    gallery, role = _get_accessible_gallery(request.user, pk, gallery_pk)
    if role != 'owner':
        messages.error(request, "Only the project owner can upload videos.")
        return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)

    # Check recording limit from SiteSettings.
    site_settings = SiteSettings.load()
    max_recordings = site_settings.max_recordings_per_project
    if max_recordings > 0 and gallery.videos.count() >= max_recordings:
        messages.error(
            request,
            f"This gallery has reached the maximum of {max_recordings} recordings.",
        )
        return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)

    form = VideoUploadForm(request.POST, request.FILES)
    if form.is_valid():
        uploaded_file = form.cleaned_data["file"]
        video = Video(
            gallery=gallery,
            file=uploaded_file,
            filename_original=uploaded_file.name,
            file_size_bytes=uploaded_file.size,
        )
        video.save()
        messages.success(request, f'Video "{uploaded_file.name}" uploaded.')
        return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)

    # Re-render detail page with form errors.
    videos = gallery.videos.order_by("-elo_rating")
    return render(request, "projects/gallery_detail.html", {
        "gallery": gallery,
        "project": gallery.project,
        "role": role,
        "is_owner": True,
        "videos": videos,
        "upload_form": form,
        "site_settings": site_settings,
    })


@login_required
def video_delete(request, pk, gallery_pk, video_id):
    """Delete a single video and its file from storage (POST only)."""
    if request.method != "POST":
        return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)

    project = get_object_or_404(Project, pk=pk, owner=request.user)
    gallery = get_object_or_404(Gallery, pk=gallery_pk, project=project)
    video = get_object_or_404(Video, pk=video_id, gallery=gallery)

    # Remove physical files.
    if video.file:
        video.file.delete(save=False)
    if video.thumbnail:
        video.thumbnail.delete(save=False)

    video.delete()
    messages.success(request, "Video deleted.")
    return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)


@login_required
def video_stream(request, pk, gallery_pk, video_id):
    """
    Serve a video file for inline playback.

    In DEBUG mode, use Django's FileResponse directly.
    In production, use X-Accel-Redirect to let nginx serve the file.
    """
    gallery, role = _get_accessible_gallery(request.user, pk, gallery_pk)
    video = get_object_or_404(Video, pk=video_id, gallery=gallery)
    return _serve_video_file(video)


@login_required
def video_download(request, pk, gallery_pk, video_id):
    """
    Serve a video file for download.

    Non-owners need at least 'commentator' role to download.
    """
    gallery, role = _get_accessible_gallery(request.user, pk, gallery_pk)
    if role not in ('owner', 'commentator'):
        raise Http404

    video = get_object_or_404(Video, pk=video_id, gallery=gallery)

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
        response = HttpResponse()
        response["Content-Type"] = "video/webm"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["X-Accel-Redirect"] = f"/protected-media/{video.file.name}"
        return response


@login_required
def download_all(request, pk, gallery_pk):
    """
    Stream all videos in a gallery as a zip archive.
    """
    gallery, role = _get_accessible_gallery(request.user, pk, gallery_pk)
    if role not in ('owner', 'commentator'):
        raise Http404

    videos = gallery.videos.all()

    if not videos.exists():
        messages.info(request, "No videos to download.")
        return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)

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
        return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)

    streamer = ZipStreamer()
    response = StreamingHttpResponse(
        streamer.stream(files),
        content_type="application/zip",
    )
    zip_filename = f"{gallery.name}.zip".replace(" ", "_")
    response["Content-Disposition"] = f'attachment; filename="{zip_filename}"'
    return response


# ---------------------------------------------------------------------------
# Project sharing
# ---------------------------------------------------------------------------


@login_required
@require_POST
def add_project_share_view(request, pk):
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

    role = request.POST.get("role", "view")
    if role not in ("view", "rank", "commentator"):
        role = "view"

    share, created = ProjectShare.objects.get_or_create(
        project=project,
        shared_with=target,
        defaults={"role": role},
    )
    if not created:
        share.role = role
        share.save()
        messages.success(request, f'Updated share for "{username}".')
    else:
        messages.success(request, f'Project shared with "{username}".')
    return redirect("projects:detail", pk=pk)


@login_required
@require_POST
def remove_project_share_view(request, pk, share_id):
    """Owner removes a user from the project share list."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    share = get_object_or_404(ProjectShare, pk=share_id, project=project)
    name = share.shared_with.username
    share.delete()
    messages.success(request, f'Removed share for "{name}".')
    return redirect("projects:detail", pk=pk)


# ---------------------------------------------------------------------------
# Gallery sharing
# ---------------------------------------------------------------------------


@login_required
@require_POST
def add_gallery_share_view(request, pk, gallery_pk):
    """Owner adds a user to the gallery share list."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    gallery = get_object_or_404(Gallery, pk=gallery_pk, project=project)
    username = request.POST.get("username", "").strip()

    if not username:
        messages.error(request, "Enter a username to share with.")
        return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)

    try:
        target = User.objects.get(username=username)
    except User.DoesNotExist:
        messages.error(request, f'User "{username}" not found.')
        return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)

    if target == request.user:
        messages.error(request, "You cannot share a gallery with yourself.")
        return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)

    role = request.POST.get("role", "view")
    if role not in ("view", "rank", "commentator"):
        role = "view"

    share, created = GalleryShare.objects.get_or_create(
        gallery=gallery,
        shared_with=target,
        defaults={"role": role},
    )
    if not created:
        share.role = role
        share.save()
        messages.success(request, f'Updated share for "{username}".')
    else:
        messages.success(request, f'Gallery shared with "{username}".')
    return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)


@login_required
@require_POST
def remove_gallery_share_view(request, pk, gallery_pk, share_id):
    """Owner removes a user from the gallery share list."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    gallery = get_object_or_404(Gallery, pk=gallery_pk, project=project)
    share = get_object_or_404(GalleryShare, pk=share_id, gallery=gallery)
    name = share.shared_with.username
    share.delete()
    messages.success(request, f'Removed gallery share for "{name}".')
    return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)


# ---------------------------------------------------------------------------
# Video comments
# ---------------------------------------------------------------------------


@login_required
def comment_list_view(request, pk, gallery_pk, video_id):
    """Return comments for a video as JSON."""
    gallery, role = _get_accessible_gallery(request.user, pk, gallery_pk)
    video = get_object_or_404(Video, pk=video_id, gallery=gallery)
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
def comment_create_view(request, pk, gallery_pk, video_id):
    """Create a comment on a video."""
    gallery, role = _get_accessible_gallery(request.user, pk, gallery_pk)
    # Non-owners need commentator role.
    if role not in ('owner', 'commentator'):
        return JsonResponse({"error": "You do not have permission to comment."}, status=403)

    video = get_object_or_404(Video, pk=video_id, gallery=gallery)

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
def comment_delete_view(request, pk, gallery_pk, video_id, comment_id):
    """Delete a comment (owner of project or comment author)."""
    gallery, role = _get_accessible_gallery(request.user, pk, gallery_pk)
    is_owner = role == 'owner'
    video = get_object_or_404(Video, pk=video_id, gallery=gallery)
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
def project_share_link_create_view(request, pk):
    """Create a project-level share link."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    access_type = request.POST.get("access_type", ShareLink.VIEW)
    raw_password = request.POST.get("password", "").strip()

    if access_type not in (ShareLink.VIEW, ShareLink.RANK, ShareLink.COMMENTATOR):
        messages.error(request, "Invalid access type.")
        return redirect("projects:detail", pk=pk)

    link = ShareLink(access_type=access_type, project=project, created_by=request.user)
    link.set_password(raw_password)
    link.save()
    messages.success(request, "Share link created.")
    return redirect("projects:detail", pk=pk)


@login_required
@require_POST
def gallery_share_link_create_view(request, pk, gallery_pk):
    """Create a gallery-level share link."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    gallery = get_object_or_404(Gallery, pk=gallery_pk, project=project)
    access_type = request.POST.get("access_type", ShareLink.VIEW)
    raw_password = request.POST.get("password", "").strip()

    if access_type not in (ShareLink.VIEW, ShareLink.RANK, ShareLink.COMMENTATOR):
        messages.error(request, "Invalid access type.")
        return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)

    link = ShareLink(access_type=access_type, gallery=gallery, created_by=request.user)
    link.set_password(raw_password)
    link.save()
    messages.success(request, "Share link created.")
    return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)


@login_required
@require_POST
def video_share_link_create_view(request, pk, gallery_pk, video_id):
    """Create a single-video share link (view or commentator only)."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    gallery = get_object_or_404(Gallery, pk=gallery_pk, project=project)
    video = get_object_or_404(Video, pk=video_id, gallery=gallery)
    access_type = request.POST.get("access_type", ShareLink.VIEW)
    raw_password = request.POST.get("password", "").strip()

    # Video links don't support rank.
    if access_type not in (ShareLink.VIEW, ShareLink.COMMENTATOR):
        access_type = ShareLink.VIEW

    link = ShareLink(access_type=access_type, video=video, created_by=request.user)
    link.set_password(raw_password)
    link.save()
    messages.success(request, "Share link created.")
    return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)


@login_required
@require_POST
def share_link_delete_view(request, pk, token):
    """Delete a share link owned by the user."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    # Link may be project, gallery, or video level — find it and verify ownership.
    link = get_object_or_404(ShareLink, token=token)
    # Ensure the link belongs to this project (directly or via gallery/video).
    is_mine = False
    if link.project_id and str(link.project_id) == str(pk):
        is_mine = True
    elif link.gallery_id and link.gallery.project_id == project.pk:
        is_mine = True
    elif link.video_id and link.video.gallery.project_id == project.pk:
        is_mine = True
    if not is_mine:
        raise Http404
    link.delete()
    messages.success(request, "Share link deleted.")
    # Redirect back to where the link was created from.
    return redirect("projects:detail", pk=pk)


# ---------------------------------------------------------------------------
# Public share link gate + viewers
# ---------------------------------------------------------------------------


def _share_destination(link, token):
    """Return the URL users land on after unlocking a share link."""
    if link.project_id:
        return _url_reverse("projects:public_project", args=[token])
    if link.gallery_id:
        if link.can_rank:
            return _url_reverse("projects:public_rank", args=[token])
        return _url_reverse("projects:public_gallery", args=[token])
    # Video link
    return _url_reverse("projects:public_video", args=[token])


def _get_unlocked_link(request, token):
    """Return the ShareLink if the session has it unlocked, else None."""
    link = get_object_or_404(ShareLink, token=token)
    if link.is_expired:
        return None
    if not link.has_password or request.session.get(_session_key(token)):
        return link
    return None


def _require_link_access(request, token):
    """
    Return (link, None) if the link is valid and unlocked,
    or (None, redirect_response) otherwise.
    """
    link = _get_unlocked_link(request, token)
    if link is None:
        return None, redirect(_url_reverse("projects:share_gate", args=[token]))
    return link, None


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

    # No password → unlock immediately and redirect.
    if not link.has_password:
        request.session[session_key] = True
        return redirect(_share_destination(link, token))

    # Already unlocked in this session.
    if request.session.get(session_key):
        return redirect(_share_destination(link, token))

    return render(request, "projects/share_gate.html", {"token": token})


def public_project_view(request, token):
    """Public project view — shows all galleries in the project."""
    link, err = _require_link_access(request, token)
    if err is not None:
        return err
    if not link.project_id:
        raise Http404
    galleries = link.project.galleries.all()
    return render(request, "projects/public_project.html", {
        "link": link,
        "project": link.project,
        "galleries": galleries,
        "token": token,
    })


def public_project_gallery_view(request, token, gallery_pk):
    """Public gallery view accessed through a project-level link."""
    link, err = _require_link_access(request, token)
    if err is not None:
        return err
    if not link.project_id:
        raise Http404
    gallery = get_object_or_404(Gallery, pk=gallery_pk, project=link.project)
    videos = gallery.videos.order_by("-elo_rating")
    return render(request, "projects/public_gallery.html", {
        "link": link,
        "project": link.project,
        "gallery": gallery,
        "videos": videos,
        "token": token,
    })


def public_gallery_view(request, token):
    """Public gallery view for a gallery-level share link."""
    link, err = _require_link_access(request, token)
    if err is not None:
        return err
    if not link.gallery_id:
        raise Http404
    gallery = link.gallery
    videos = gallery.videos.order_by("-elo_rating")
    return render(request, "projects/public_gallery.html", {
        "link": link,
        "project": gallery.project,
        "gallery": gallery,
        "videos": videos,
        "token": token,
    })


def public_gallery_video_stream(request, token, video_id):
    """Serve a video file for the public gallery."""
    link, err = _require_link_access(request, token)
    if err is not None:
        raise Http404
    # Resolve the gallery from either a project or gallery link.
    if link.gallery_id:
        gallery = link.gallery
    elif link.project_id:
        # Find which gallery this video belongs to (within the project).
        video = get_object_or_404(Video, pk=video_id, gallery__project=link.project)
        return _serve_video_file(video)
    else:
        raise Http404
    video = get_object_or_404(Video, pk=video_id, gallery=gallery)
    return _serve_video_file(video)


def public_video_view(request, token):
    """Public view for a single video link."""
    link, err = _require_link_access(request, token)
    if err is not None:
        return err
    if not link.video_id:
        raise Http404
    video = link.video
    return render(request, "projects/public_video.html", {
        "link": link,
        "video": video,
        "token": token,
    })


def public_video_stream(request, token):
    """Serve the video file for a single-video public link."""
    link, err = _require_link_access(request, token)
    if err is not None:
        raise Http404
    if not link.video_id:
        raise Http404
    return _serve_video_file(link.video)


def public_rank_view(request, token):
    """Public ranking page (gallery-level link with rank/commentator access)."""
    link, err = _require_link_access(request, token)
    if err is not None:
        return err
    if not link.gallery_id or not link.can_rank:
        raise Http404
    from recording.ranking import get_ranking_progress
    progress = get_ranking_progress(link.gallery_id)
    return render(request, "projects/public_rank.html", {
        "link": link,
        "gallery": link.gallery,
        "project": link.gallery.project,
        "progress": progress,
        "token": token,
    })


@require_GET
def public_next_pair_view(request, token):
    """Public API: get next pair to compare."""
    link, err = _require_link_access(request, token)
    if err is not None:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    if not link.gallery_id or not link.can_rank:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    from recording.ranking import get_ranking_progress, select_next_pair
    progress = get_ranking_progress(link.gallery_id)
    pair = select_next_pair(link.gallery_id)
    if pair is None:
        return JsonResponse({"complete": True, "progress": progress})
    video_a, video_b = pair
    return JsonResponse({
        "complete": False,
        "video_left": {
            "id": str(video_a.id),
            "url": _url_reverse("projects:public_rank_video_file", args=[token, video_a.id]),
            "name": video_a.filename_original or str(video_a.id),
            "elo": round(video_a.elo_rating, 1),
        },
        "video_right": {
            "id": str(video_b.id),
            "url": _url_reverse("projects:public_rank_video_file", args=[token, video_b.id]),
            "name": video_b.filename_original or str(video_b.id),
            "elo": round(video_b.elo_rating, 1),
        },
        "progress": progress,
    })


@require_POST
def public_submit_comparison_view(request, token):
    """Public API: submit a comparison result."""
    link, err = _require_link_access(request, token)
    if err is not None:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    if not link.gallery_id or not link.can_rank:
        return JsonResponse({"error": "Unauthorized"}, status=403)

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
    gallery = link.gallery
    video_left = get_object_or_404(Video, pk=video_left_id, gallery=gallery)
    video_right = get_object_or_404(Video, pk=video_right_id, gallery=gallery)
    Comparison.objects.create(
        gallery=gallery,
        video_left=video_left,
        video_right=video_right,
        result=result,
        user=None,
    )
    update_elo(video_left, video_right, result)
    return JsonResponse({"success": True})


def public_rank_video_file(request, token, video_id):
    """Serve a video file for the public ranking page."""
    link, err = _require_link_access(request, token)
    if err is not None:
        raise Http404
    if not link.gallery_id or not link.can_rank:
        raise Http404
    video = get_object_or_404(Video, pk=video_id, gallery=link.gallery)
    return _serve_video_file(video)


def _serve_video_file(video):
    """Shared helper: serve a Video's file via FileResponse (debug) or X-Accel-Redirect (prod)."""
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
