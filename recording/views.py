import json
import os
import shutil
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from projects.models import Gallery, Video
from recording.models import (
    Comparison,
    KeybindPreference,
    RecordingSession,
    RecordingSettings,
)
from django.urls import reverse
from recording.ranking import get_ranking_progress, select_next_pair, update_elo


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@login_required
@require_POST
def start_session(request, gallery_id):
    """Create a new RecordingSession and return its token + QR URL."""
    gallery = get_object_or_404(Gallery, pk=gallery_id)
    if gallery.project.owner != request.user:
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    # Deactivate any existing sessions for this gallery/user.
    RecordingSession.objects.filter(
        gallery=gallery, user=request.user, is_active=True,
    ).update(is_active=False)

    session = RecordingSession.objects.create(
        gallery=gallery,
        user=request.user,
        expires_at=timezone.now() + timedelta(hours=1),
    )

    qr_url = request.build_absolute_uri(f'/recording/phone/{session.token}/')

    return JsonResponse({
        'session_id': str(session.id),
        'token': session.token,
        'qr_url': qr_url,
    })


# ---------------------------------------------------------------------------
# Phone recording endpoints (token-authenticated, no CSRF)
# ---------------------------------------------------------------------------

def _get_valid_session(token):
    """Return an active, non-expired RecordingSession or None."""
    try:
        session = RecordingSession.objects.select_related('user').get(
            token=token, is_active=True,
        )
    except RecordingSession.DoesNotExist:
        return None
    if session.expires_at < timezone.now():
        return None
    return session


@require_GET
def phone_recorder(request, token):
    """Render the phone recording UI. Token serves as authentication."""
    session = _get_valid_session(token)
    if session is None:
        return JsonResponse({'error': 'Invalid or expired session.'}, status=403)

    # Load the session owner's recording settings (or defaults).
    rec_settings, _ = RecordingSettings.objects.get_or_create(user=session.user)

    return render(request, 'recording/phone_recorder.html', {
        'token': token,
        'session_id': str(session.id),
        'settings': {
            'video_resolution': rec_settings.video_resolution,
            'frame_rate': rec_settings.frame_rate,
            'video_codec': rec_settings.video_codec,
            'audio_enabled': rec_settings.audio_enabled,
            'audio_codec': rec_settings.audio_codec,
            'audio_bitrate': rec_settings.audio_bitrate,
        },
    })


@csrf_exempt
@require_POST
def phone_chunk_upload(request, token):
    """
    Receive a video chunk and write it to a per-chunk file.

    Idempotent: re-uploading the same chunk_index overwrites the existing
    chunk with identical data, so client retries never produce duplicate
    bytes in the final assembled file (a common cause of corruption).
    """
    session = _get_valid_session(token)
    if session is None:
        return JsonResponse({'error': 'Invalid or expired session.'}, status=403)

    # Chunk can arrive as FormData (with 'chunk' file) or as raw body.
    chunk_file = request.FILES.get('chunk')
    if chunk_file:
        chunk_data = chunk_file.read()
    else:
        chunk_data = request.body

    if not chunk_data:
        return JsonResponse({'error': 'Empty chunk.'}, status=400)

    # Parse chunk_index; default to a monotonic value if missing.
    try:
        chunk_index = int(request.POST.get('chunk_index', '0'))
    except (TypeError, ValueError):
        chunk_index = 0
    if chunk_index < 0 or chunk_index > 1_000_000:
        return JsonResponse({'error': 'Invalid chunk_index.'}, status=400)

    # Write each chunk to its own file inside a per-session dir.
    session_dir = os.path.join(settings.MEDIA_ROOT, 'temp', str(session.id))
    os.makedirs(session_dir, exist_ok=True)
    chunk_path = os.path.join(session_dir, f'chunk_{chunk_index:06d}.bin')
    with open(chunk_path, 'wb') as f:  # write, not append → idempotent
        f.write(chunk_data)

    # Extend session expiry on each successful chunk upload.
    session.expires_at = timezone.now() + timedelta(hours=1)
    session.save(update_fields=['expires_at'])

    return JsonResponse({'success': True})


def _extension_from_mime(mime_type):
    """Map a MediaRecorder mimeType string to a file extension."""
    m = (mime_type or '').lower()
    if 'mp4' in m:
        return 'mp4'
    if 'ogg' in m:
        return 'ogg'
    return 'webm'


@csrf_exempt
@require_POST
def phone_finalize(request, token):
    """
    Concatenate all uploaded chunks in order, save with the correct
    extension based on the recorder's MIME type, and create a Video row.
    """
    session = _get_valid_session(token)
    if session is None:
        return JsonResponse({'error': 'Invalid or expired session.'}, status=403)

    # Read mime_type from request body so we can pick the right extension.
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}
    ext = _extension_from_mime(body.get('mime_type', ''))

    session_dir = os.path.join(settings.MEDIA_ROOT, 'temp', str(session.id))
    legacy_path = os.path.join(settings.MEDIA_ROOT, 'temp', f'{session.id}.webm')

    # Gather chunks in index order.
    chunk_files = []
    if os.path.isdir(session_dir):
        chunk_files = sorted(
            os.path.join(session_dir, name)
            for name in os.listdir(session_dir)
            if name.startswith('chunk_') and name.endswith('.bin')
        )

    if not chunk_files and not os.path.exists(legacy_path):
        return JsonResponse({'error': 'No recording data found.'}, status=404)

    video_id = uuid.uuid4()
    project_id = session.gallery.project_id
    final_dir = os.path.join(settings.MEDIA_ROOT, 'videos', str(project_id))
    os.makedirs(final_dir, exist_ok=True)
    final_path = os.path.join(final_dir, f'{video_id}.{ext}')

    if chunk_files:
        # Concatenate chunks in order.
        with open(final_path, 'wb') as out:
            for chunk_path in chunk_files:
                with open(chunk_path, 'rb') as src:
                    shutil.copyfileobj(src, out, length=1024 * 1024)
        # Clean up chunk dir.
        try:
            shutil.rmtree(session_dir)
        except OSError:
            pass
    else:
        # Fallback: legacy single-file temp from before this fix.
        shutil.move(legacy_path, final_path)

    relative_path = f'videos/{project_id}/{video_id}.{ext}'
    file_size = os.path.getsize(final_path)

    video = Video.objects.create(
        id=video_id,
        gallery=session.gallery,
        file=relative_path,
        filename_original=f'{video_id}.{ext}',
        file_size_bytes=file_size,
    )

    return JsonResponse({'success': True, 'video_id': str(video.id)})


@csrf_exempt
@require_POST
def phone_discard(request, token):
    """Delete any temp data for this session (chunk dir and legacy single file)."""
    session = _get_valid_session(token)
    if session is None:
        return JsonResponse({'error': 'Invalid or expired session.'}, status=403)

    session_dir = os.path.join(settings.MEDIA_ROOT, 'temp', str(session.id))
    legacy_path = os.path.join(settings.MEDIA_ROOT, 'temp', f'{session.id}.webm')
    if os.path.isdir(session_dir):
        try:
            shutil.rmtree(session_dir)
        except OSError:
            pass
    if os.path.exists(legacy_path):
        try:
            os.remove(legacy_path)
        except OSError:
            pass

    return JsonResponse({'success': True})


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

@login_required
@require_GET
def rank_view(request, gallery_id):
    """Render the ranking comparison page."""
    gallery = get_object_or_404(Gallery, pk=gallery_id)
    if gallery.project.owner != request.user:
        from django.http import Http404
        raise Http404
    progress = get_ranking_progress(gallery_id)
    return render(request, 'recording/rank.html', {
        'gallery': gallery,
        'project': gallery.project,
        'progress': progress,
    })


@login_required
@require_GET
def next_pair(request, gallery_id):
    """Return the next pair of videos to compare, or signal completion."""
    gallery = get_object_or_404(Gallery, pk=gallery_id)
    if gallery.project.owner != request.user:
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    progress = get_ranking_progress(gallery_id)
    pair = select_next_pair(gallery_id)
    if pair is None:
        return JsonResponse({'complete': True, 'progress': progress})

    video_a, video_b = pair
    return JsonResponse({
        'complete': False,
        'video_left': {
            'id': str(video_a.id),
            'url': reverse('projects:video_stream', args=[gallery.project_id, gallery.id, video_a.id]),
            'name': video_a.filename_original or str(video_a.id),
            'elo': round(video_a.elo_rating, 1),
        },
        'video_right': {
            'id': str(video_b.id),
            'url': reverse('projects:video_stream', args=[gallery.project_id, gallery.id, video_b.id]),
            'name': video_b.filename_original or str(video_b.id),
            'elo': round(video_b.elo_rating, 1),
        },
        'progress': progress,
    })


@login_required
@require_POST
def submit_comparison(request, gallery_id):
    """Accept a comparison result, create the record, and update Elo."""
    gallery = get_object_or_404(Gallery, pk=gallery_id)
    if gallery.project.owner != request.user:
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON.'}, status=400)

    video_left_id = data.get('video_left')
    video_right_id = data.get('video_right')
    result = data.get('result')

    if result not in ('left', 'right', 'equal'):
        return JsonResponse({'error': 'Invalid result.'}, status=400)

    video_left = get_object_or_404(Video, pk=video_left_id, gallery=gallery)
    video_right = get_object_or_404(Video, pk=video_right_id, gallery=gallery)

    Comparison.objects.create(
        gallery=gallery,
        video_left=video_left,
        video_right=video_right,
        result=result,
        user=request.user,
    )

    update_elo(video_left, video_right, result)

    return JsonResponse({'success': True})


# ---------------------------------------------------------------------------
# User preferences
# ---------------------------------------------------------------------------

@login_required
def keybind_view(request):
    """GET: return current keybinds. POST: update keybinds."""
    prefs, _ = KeybindPreference.objects.get_or_create(user=request.user)

    if request.method == 'GET':
        return JsonResponse({
            'start_stop_key': prefs.start_stop_key,
            'discard_key': prefs.discard_key,
        })

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)

        if 'start_stop_key' in data:
            prefs.start_stop_key = data['start_stop_key']
        if 'discard_key' in data:
            prefs.discard_key = data['discard_key']
        prefs.save()

        return JsonResponse({'success': True})

    return JsonResponse({'error': 'Method not allowed.'}, status=405)


@login_required
def recording_settings_view(request):
    """GET: render settings page (or return JSON for AJAX). POST: update settings."""
    rec_settings, _ = RecordingSettings.objects.get_or_create(user=request.user)

    if request.method == 'GET':
        # Return JSON for AJAX requests, HTML for normal browser requests.
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'video_resolution': rec_settings.video_resolution,
                'frame_rate': rec_settings.frame_rate,
                'video_codec': rec_settings.video_codec,
                'audio_enabled': rec_settings.audio_enabled,
                'audio_codec': rec_settings.audio_codec,
                'audio_bitrate': rec_settings.audio_bitrate,
            })
        return render(request, 'recording/recording_settings.html', {
            'settings': rec_settings,
        })

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)

        allowed_fields = [
            'video_resolution', 'frame_rate', 'video_codec',
            'audio_enabled', 'audio_codec', 'audio_bitrate',
        ]
        for field in allowed_fields:
            if field in data:
                setattr(rec_settings, field, data[field])
        rec_settings.save()

        return JsonResponse({'success': True})

    return JsonResponse({'error': 'Method not allowed.'}, status=405)


# ---------------------------------------------------------------------------
# Desktop recording control page
# ---------------------------------------------------------------------------

@login_required
@require_GET
def record_control(request, gallery_id):
    """Render the desktop recording control page."""
    gallery = get_object_or_404(Gallery, pk=gallery_id)
    if gallery.project.owner != request.user:
        from django.http import Http404
        raise Http404
    return render(request, 'recording/record_control.html', {
        'gallery': gallery,
        'project': gallery.project,
    })
