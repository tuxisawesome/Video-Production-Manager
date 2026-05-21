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
from django.views.decorators.csrf import csrf_exempt
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
    share_links = project.share_links.all() if is_owner else []

    return render(request, "projects/project_detail.html", {
        "project": project,
        "is_owner": is_owner,
        "galleries": galleries,
        "shares": shares,
        "share_links": share_links,
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
    if is_owner:
        videos = gallery.videos.prefetch_related('share_links').order_by("-elo_rating")
    else:
        videos = gallery.videos.order_by("-elo_rating")
    upload_form = VideoUploadForm()
    site_settings = SiteSettings.load()
    shares = project.shares.select_related("shared_with").all() if is_owner else []
    gallery_shares = gallery.shares.select_related("shared_with").all() if is_owner else []
    share_links = gallery.share_links.all() if is_owner else []

    # Flat list of every video share link in this gallery — used by the
    # owner-only "All video share links" table so they can manage links
    # without opening each video sidebar in turn.
    video_share_rows = []
    if is_owner:
        for v in videos:
            for sl in v.share_links.all():
                video_share_rows.append({"video": v, "link": sl})

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
        "video_share_rows": video_share_rows,
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
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True})
    messages.success(request, "Video deleted.")
    return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)


@login_required
@require_POST
def video_rename(request, pk, gallery_pk, video_id):
    """Update filename_original on a video. Owner only."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    gallery = get_object_or_404(Gallery, pk=gallery_pk, project=project)
    video = get_object_or_404(Video, pk=video_id, gallery=gallery)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    new_name = (data.get("name") or "").strip()
    if not new_name:
        return JsonResponse({"error": "Name is required."}, status=400)
    if len(new_name) > 255:
        return JsonResponse({"error": "Name is too long (max 255 chars)."}, status=400)

    video.filename_original = new_name
    video.save(update_fields=["filename_original"])
    return JsonResponse({"success": True, "name": new_name})


@login_required
@require_POST
def video_move(request, pk, gallery_pk, video_id):
    """
    Move a video to a different gallery (any project the user owns). The
    physical file is relocated under the target project's media dir when
    crossing projects; share links FK to the video, so they follow
    automatically and remain valid.
    """
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    gallery = get_object_or_404(Gallery, pk=gallery_pk, project=project)
    video = get_object_or_404(Video, pk=video_id, gallery=gallery)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    target_gallery_id = data.get("target_gallery_id")
    if not target_gallery_id:
        return JsonResponse({"error": "target_gallery_id is required."}, status=400)

    try:
        target_gallery = Gallery.objects.select_related("project").get(pk=target_gallery_id)
    except (Gallery.DoesNotExist, ValueError):
        return JsonResponse({"error": "Target gallery not found."}, status=404)

    if target_gallery.project.owner != request.user:
        return JsonResponse({"error": "You do not own the target project."}, status=403)

    if str(target_gallery.id) == str(gallery.id):
        return JsonResponse({"error": "Video is already in that gallery."}, status=400)

    _relocate_video(video, target_gallery)

    return JsonResponse({
        "success": True,
        "target_project_id": str(target_gallery.project_id),
        "target_gallery_id": str(target_gallery.id),
    })


def _relocate_video(video, target_gallery):
    """
    Physically move *video*'s file (and thumbnail) to the target gallery's
    project directory, then update the FK. Called by video_move and
    video_bulk_move.

    Comparisons that referenced the video stay attached to the OLD gallery,
    which means the moved video disappears from the old gallery's ranking
    history but stays correctly attributed in any leaderboard / Elo lookup
    that walks Video.elo_rating directly.
    """
    src_path = video.file.path if video.file else None

    if str(target_gallery.project_id) != str(video.gallery.project_id):
        # Cross-project move: relocate the file on disk.
        ext = os.path.splitext(video.file.name)[1].lstrip(".").lower() or "webm"
        new_relative = f"videos/{target_gallery.project_id}/{video.id}.{ext}"
        new_abs = os.path.join(settings.MEDIA_ROOT, new_relative)
        os.makedirs(os.path.dirname(new_abs), exist_ok=True)
        if src_path and os.path.isfile(src_path):
            os.replace(src_path, new_abs)
        video.file.name = new_relative

        # Thumbnails live in MEDIA/thumbnails/ — no per-project nesting, so
        # they don't need to move. They keep their FileField path unchanged.

    video.gallery = target_gallery
    # Skip the save() auto-rename: we've already set the path correctly.
    video.save()


@login_required
@require_POST
def video_bulk_move(request):
    """Move many videos to one target gallery in a single request."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    video_ids = data.get("video_ids") or []
    target_gallery_id = data.get("target_gallery_id")
    if not video_ids or not target_gallery_id:
        return JsonResponse({"error": "video_ids and target_gallery_id required."}, status=400)
    if len(video_ids) > 200:
        return JsonResponse({"error": "Too many videos in one move."}, status=400)

    try:
        target_gallery = Gallery.objects.select_related("project").get(pk=target_gallery_id)
    except (Gallery.DoesNotExist, ValueError):
        return JsonResponse({"error": "Target gallery not found."}, status=404)
    if target_gallery.project.owner != request.user:
        return JsonResponse({"error": "You do not own the target project."}, status=403)

    moved, skipped = 0, 0
    for vid in video_ids:
        try:
            v = Video.objects.select_related("gallery__project").get(pk=vid)
        except (Video.DoesNotExist, ValueError):
            skipped += 1
            continue
        if v.gallery.project.owner != request.user:
            skipped += 1
            continue
        if str(v.gallery_id) == str(target_gallery.id):
            skipped += 1
            continue
        _relocate_video(v, target_gallery)
        moved += 1

    return JsonResponse({"success": True, "moved": moved, "skipped": skipped})


@login_required
@require_POST
def video_bulk_delete(request):
    """Delete many videos in a single request."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    video_ids = data.get("video_ids") or []
    if not video_ids:
        return JsonResponse({"error": "video_ids required."}, status=400)
    if len(video_ids) > 200:
        return JsonResponse({"error": "Too many videos in one delete."}, status=400)

    deleted = 0
    for vid in video_ids:
        try:
            v = Video.objects.select_related("gallery__project").get(pk=vid)
        except (Video.DoesNotExist, ValueError):
            continue
        if v.gallery.project.owner != request.user:
            continue
        if v.file:
            v.file.delete(save=False)
        if v.thumbnail:
            v.thumbnail.delete(save=False)
        v.delete()
        deleted += 1

    return JsonResponse({"success": True, "deleted": deleted})


@login_required
@require_GET
def gallery_picker_list(request):
    """Return all galleries the user owns, grouped by project. Used by the
    Move-Videos picker dialog."""
    galleries = (
        Gallery.objects
        .filter(project__owner=request.user)
        .select_related("project")
        .order_by("project__name", "name")
    )
    out = [
        {
            "gallery_id":   str(g.pk),
            "gallery_name": g.name,
            "project_id":   str(g.project_id),
            "project_name": g.project.name,
        }
        for g in galleries
    ]
    return JsonResponse({"galleries": out})


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

    filename = video.filename_original or video.file.name.split("/")[-1]
    content_type = _video_content_type(video.file.name)

    if settings.DEBUG:
        file_path = video.file.path
        if not os.path.isfile(file_path):
            raise Http404("Video file not found on disk.")
        response = FileResponse(open(file_path, "rb"), content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    else:
        response = HttpResponse()
        response["Content-Type"] = content_type
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
            "author": c.display_author,
            "is_guest": not c.author_id,
            "text": c.text,
            "timestamp_seconds": c.timestamp_seconds,
            "created_at": c.created_at.isoformat(),
            "is_own": c.author_id is not None and c.author_id == request.user.id,
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

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if is_ajax:
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON."}, status=400)
        access_type = body.get("access_type", ShareLink.VIEW)
        raw_password = body.get("password", "").strip()
    else:
        access_type = request.POST.get("access_type", ShareLink.VIEW)
        raw_password = request.POST.get("password", "").strip()

    # Video links don't support rank.
    if access_type not in (ShareLink.VIEW, ShareLink.COMMENTATOR):
        access_type = ShareLink.VIEW

    link = ShareLink(access_type=access_type, video=video, created_by=request.user)
    link.set_password(raw_password)
    link.save()

    if is_ajax:
        return JsonResponse({
            "token": str(link.token),
            "access_type_display": link.get_access_type_display(),
            "has_password": link.has_password,
            "url": request.build_absolute_uri(
                _url_reverse("projects:share_gate", args=[link.token])
            ),
            "delete_url": _url_reverse("projects:share_link_delete", args=[pk, link.token]),
        })

    messages.success(request, "Share link created.")
    return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)


@login_required
@require_GET
def video_share_link_list_view(request, pk, gallery_pk, video_id):
    """Return existing share links for a video as JSON (owner only)."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    gallery = get_object_or_404(Gallery, pk=gallery_pk, project=project)
    video = get_object_or_404(Video, pk=video_id, gallery=gallery)
    links = [
        {
            "token": str(sl.token),
            "access_type_display": sl.get_access_type_display(),
            "has_password": sl.has_password,
            "url": request.build_absolute_uri(
                _url_reverse("projects:share_gate", args=[sl.token])
            ),
            "delete_url": _url_reverse("projects:share_link_delete", args=[pk, sl.token]),
        }
        for sl in video.share_links.all()
    ]
    return JsonResponse({"links": links})


@login_required
@require_POST
def share_link_delete_view(request, pk, token):
    """Delete a share link owned by the user."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)
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

    # Capture redirect target before deletion.
    gallery_pk = link.gallery_id or (link.video.gallery_id if link.video_id else None)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    link.delete()

    if is_ajax:
        return JsonResponse({"success": True})

    messages.success(request, "Share link deleted.")
    if gallery_pk:
        return redirect("projects:gallery_detail", pk=pk, gallery_pk=gallery_pk)
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


# ---------------------------------------------------------------------------
# Public (share-link) comment endpoints
# ---------------------------------------------------------------------------


def _public_video_for_comments(request, token):
    """
    Resolve the Video targeted by a public share token, or return an error.
    Works for video-level share links (link.video) and gallery-level links
    (where the video id arrives in the request body / query).
    """
    link, err = _require_link_access(request, token)
    if err is not None:
        return None, None, err

    if link.video_id:
        return link, link.video, None

    # Gallery- or project-level link — must specify which video.
    if request.method == "POST":
        try:
            body = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return None, None, JsonResponse({"error": "Invalid JSON."}, status=400)
        vid = body.get("video_id")
    else:
        vid = request.GET.get("video_id")

    if not vid:
        return None, None, JsonResponse({"error": "video_id required."}, status=400)

    if link.gallery_id:
        try:
            video = Video.objects.get(pk=vid, gallery_id=link.gallery_id)
        except (Video.DoesNotExist, ValueError):
            return None, None, JsonResponse({"error": "Video not found."}, status=404)
    elif link.project_id:
        try:
            video = Video.objects.get(pk=vid, gallery__project_id=link.project_id)
        except (Video.DoesNotExist, ValueError):
            return None, None, JsonResponse({"error": "Video not found."}, status=404)
    else:
        return None, None, JsonResponse({"error": "Bad link."}, status=400)

    return link, video, None


@require_GET
def public_comment_list_view(request, token):
    """List comments on a video reached via a public share link."""
    link, video, err = _public_video_for_comments(request, token)
    if err is not None:
        return err

    comments = [
        {
            "id": c.id,
            "author": c.display_author,
            "is_guest": not c.author_id,
            "text": c.text,
            "timestamp_seconds": c.timestamp_seconds,
            "created_at": c.created_at.isoformat(),
            "is_own": False,  # guests can't delete their own comments
        }
        for c in video.comments.select_related("author").all()
    ]
    return JsonResponse({"comments": comments})


@csrf_exempt  # share-link auth is via the URL token, not session/CSRF.
@require_POST
def public_comment_create_view(request, token):
    """Create a comment on a video via a public commentator share link."""
    link, video, err = _public_video_for_comments(request, token)
    if err is not None:
        return err

    if not link.can_comment:
        return JsonResponse(
            {"error": "This share link does not allow commenting."}, status=403,
        )

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    text = (data.get("text") or "").strip()
    if not text:
        return JsonResponse({"error": "Comment text is required."}, status=400)
    if len(text) > 4000:
        return JsonResponse({"error": "Comment is too long."}, status=400)

    guest_name = (data.get("guest_name") or "").strip()[:80]

    ts = data.get("timestamp_seconds")
    if ts is not None:
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            ts = None

    comment = VideoComment.objects.create(
        video=video,
        author=None,           # guest
        guest_name=guest_name,
        share_link_token=str(link.token),
        text=text,
        timestamp_seconds=ts,
    )
    return JsonResponse({
        "id": comment.id,
        "author": comment.display_author,
        "is_guest": True,
        "text": comment.text,
        "timestamp_seconds": comment.timestamp_seconds,
        "created_at": comment.created_at.isoformat(),
        "is_own": False,
    }, status=201)


def public_video_stream(request, token):
    """Serve the video file for a single-video public link."""
    link, err = _require_link_access(request, token)
    if err is not None:
        raise Http404
    if not link.video_id:
        raise Http404
    return _serve_video_file(link.video)


def _resolve_rank_gallery(link, request, body=None):
    """
    Pick the Gallery to rank within for a public share link.

    Gallery-level links carry their own gallery — return it directly.
    Project-level links don't, so the caller must pass ?gallery=<uuid>
    (GET) or {"gallery": ...} (POST body) identifying which gallery
    within the project to rank. We verify the gallery belongs to that
    project so a token can't be used to rank in someone else's gallery.

    Returns the Gallery or None.
    """
    if link.gallery_id:
        return link.gallery
    if not link.project_id:
        return None
    gallery_id = (body or {}).get("gallery") if body else None
    if not gallery_id:
        gallery_id = request.GET.get("gallery")
    if not gallery_id:
        return None
    try:
        return Gallery.objects.select_related("project").get(
            pk=gallery_id, project_id=link.project_id,
        )
    except (Gallery.DoesNotExist, ValueError):
        return None


def public_rank_view(request, token):
    """Public ranking page. Accepts gallery-level and project-level links."""
    link, err = _require_link_access(request, token)
    if err is not None:
        return err
    if not link.can_rank:
        raise Http404
    gallery = _resolve_rank_gallery(link, request)
    if gallery is None:
        # Project-level link without ?gallery= — bounce the user back to the
        # project page so they can pick which gallery to rank.
        if link.project_id:
            return redirect("projects:public_project", token=token)
        raise Http404
    from recording.ranking import get_ranking_progress
    progress = get_ranking_progress(gallery.id)
    return render(request, "projects/public_rank.html", {
        "link": link,
        "gallery": gallery,
        "project": gallery.project,
        "progress": progress,
        "token": token,
    })


@require_GET
def public_next_pair_view(request, token):
    """Public API: get next pair to compare."""
    link, err = _require_link_access(request, token)
    if err is not None:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    if not link.can_rank:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    gallery = _resolve_rank_gallery(link, request)
    if gallery is None:
        return JsonResponse({"error": "gallery required"}, status=400)
    from recording.ranking import get_ranking_progress, select_next_pair
    progress = get_ranking_progress(gallery.id)
    pair = select_next_pair(gallery.id)
    if pair is None:
        return JsonResponse({"complete": True, "progress": progress})
    video_a, video_b = pair
    # Pass the gallery through so the player URL stays scoped correctly when
    # ranking from a project-level link.
    qs = f"?gallery={gallery.id}" if not link.gallery_id else ""
    return JsonResponse({
        "complete": False,
        "video_left": {
            "id": str(video_a.id),
            "url": _url_reverse("projects:public_rank_video_file", args=[token, video_a.id]) + qs,
            "name": video_a.filename_original or str(video_a.id),
            "elo": round(video_a.elo_rating, 1),
        },
        "video_right": {
            "id": str(video_b.id),
            "url": _url_reverse("projects:public_rank_video_file", args=[token, video_b.id]) + qs,
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
    if not link.can_rank:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    gallery = _resolve_rank_gallery(link, request, body=data)
    if gallery is None:
        return JsonResponse({"error": "gallery required"}, status=400)

    video_left_id = data.get("video_left")
    video_right_id = data.get("video_right")
    result = data.get("result")
    if result not in ("left", "right", "equal"):
        return JsonResponse({"error": "Invalid result."}, status=400)

    from recording.models import Comparison
    from recording.ranking import update_elo
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
    if not link.can_rank:
        raise Http404
    gallery = _resolve_rank_gallery(link, request)
    if gallery is None:
        raise Http404
    video = get_object_or_404(Video, pk=video_id, gallery=gallery)
    return _serve_video_file(video)


def _video_content_type(file_name):
    """Return the correct MIME type based on the video file's extension."""
    name = (file_name or "").lower()
    if name.endswith(".mp4"):
        return "video/mp4"
    if name.endswith(".ogg") or name.endswith(".ogv"):
        return "video/ogg"
    return "video/webm"


def _serve_video_file(video):
    """Shared helper: serve a Video's file via FileResponse (debug) or X-Accel-Redirect (prod)."""
    if not video.file:
        raise Http404
    content_type = _video_content_type(video.file.name)
    if settings.DEBUG:
        file_path = video.file.path
        if not os.path.isfile(file_path):
            raise Http404
        return FileResponse(open(file_path, "rb"), content_type=content_type)
    response = HttpResponse()
    response["Content-Type"] = content_type
    response["X-Accel-Redirect"] = f"/protected-media/{video.file.name}"
    return response
