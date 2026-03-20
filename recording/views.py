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

from projects.models import Project, Video
from recording.models import (
    Comparison,
    KeybindPreference,
    RecordingSession,
    RecordingSettings,
)
from recording.ranking import get_ranking_progress, select_next_pair, update_elo


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@login_required
@require_POST
def start_session(request, project_id):
    """Create a new RecordingSession and return its token + QR URL."""
    project = get_object_or_404(Project, pk=project_id, owner=request.user)

    # Deactivate any existing sessions for this project/user.
    RecordingSession.objects.filter(
        project=project, user=request.user, is_active=True,
    ).update(is_active=False)

    session = RecordingSession.objects.create(
        project=project,
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
    """Receive a video chunk from the phone and append it to a temp file."""
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

    temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp')
    os.makedirs(temp_dir, exist_ok=True)

    temp_path = os.path.join(temp_dir, f'{session.id}.webm')
    with open(temp_path, 'ab') as f:
        f.write(chunk_data)

    # Extend session expiry on each successful chunk upload.
    session.expires_at = timezone.now() + timedelta(hours=1)
    session.save(update_fields=['expires_at'])

    return JsonResponse({'success': True})


@csrf_exempt
@require_POST
def phone_finalize(request, token):
    """Move the temp recording to its final location and create a Video."""
    session = _get_valid_session(token)
    if session is None:
        return JsonResponse({'error': 'Invalid or expired session.'}, status=403)

    temp_path = os.path.join(settings.MEDIA_ROOT, 'temp', f'{session.id}.webm')
    if not os.path.exists(temp_path):
        return JsonResponse({'error': 'No recording data found.'}, status=404)

    video_id = uuid.uuid4()
    final_dir = os.path.join(settings.MEDIA_ROOT, 'videos', str(session.project_id))
    os.makedirs(final_dir, exist_ok=True)
    final_path = os.path.join(final_dir, f'{video_id}.webm')

    shutil.move(temp_path, final_path)

    relative_path = f'videos/{session.project_id}/{video_id}.webm'
    file_size = os.path.getsize(final_path)

    video = Video.objects.create(
        id=video_id,
        project=session.project,
        file=relative_path,
        filename_original=f'{video_id}.webm',
        file_size_bytes=file_size,
    )

    return JsonResponse({'success': True, 'video_id': str(video.id)})


@csrf_exempt
@require_POST
def phone_discard(request, token):
    """Delete the temp recording file if it exists."""
    session = _get_valid_session(token)
    if session is None:
        return JsonResponse({'error': 'Invalid or expired session.'}, status=403)

    temp_path = os.path.join(settings.MEDIA_ROOT, 'temp', f'{session.id}.webm')
    if os.path.exists(temp_path):
        os.remove(temp_path)

    return JsonResponse({'success': True})


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

@login_required
@require_GET
def rank_view(request, project_id):
    """Render the ranking comparison page."""
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    progress = get_ranking_progress(project_id)
    return render(request, 'recording/rank.html', {
        'project': project,
        'progress': progress,
    })


@login_required
@require_GET
def next_pair(request, project_id):
    """Return the next pair of videos to compare, or signal completion."""
    get_object_or_404(Project, pk=project_id, owner=request.user)

    progress = get_ranking_progress(project_id)
    pair = select_next_pair(project_id)
    if pair is None:
        return JsonResponse({'complete': True, 'progress': progress})

    video_a, video_b = pair
    return JsonResponse({
        'complete': False,
        'video_left': {
            'id': str(video_a.id),
            'url': video_a.file.url,
            'name': video_a.filename_original or str(video_a.id),
            'elo': round(video_a.elo_rating, 1),
        },
        'video_right': {
            'id': str(video_b.id),
            'url': video_b.file.url,
            'name': video_b.filename_original or str(video_b.id),
            'elo': round(video_b.elo_rating, 1),
        },
        'progress': progress,
    })


@login_required
@require_POST
def submit_comparison(request, project_id):
    """Accept a comparison result, create the record, and update Elo."""
    project = get_object_or_404(Project, pk=project_id, owner=request.user)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON.'}, status=400)

    video_left_id = data.get('video_left')
    video_right_id = data.get('video_right')
    result = data.get('result')

    if result not in ('left', 'right', 'equal'):
        return JsonResponse({'error': 'Invalid result.'}, status=400)

    video_left = get_object_or_404(Video, pk=video_left_id, project=project)
    video_right = get_object_or_404(Video, pk=video_right_id, project=project)

    Comparison.objects.create(
        project=project,
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
def record_control(request, project_id):
    """Render the desktop recording control page."""
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    return render(request, 'recording/record_control.html', {
        'project': project,
    })
