"""
Export and import utilities for full-site data migration.

Export produces a zip containing JSON data files and all media (videos + thumbnails).
Import reads such a zip and restores everything to a new server.
"""

import json
import os
import time
import zipfile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import SiteSettings
from projects.models import Project, Video
from projects.utils import _StreamingBuffer
from recording.models import Comparison, KeybindPreference, RecordingSettings

User = get_user_model()

EXPORT_VERSION = 1


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def _serialize_users():
    users = []
    for u in User.objects.all():
        users.append({
            'username': u.username,
            'email': u.email,
            'password': u.password,
            'is_staff': u.is_staff,
            'is_superuser': u.is_superuser,
            'is_active': u.is_active,
            'max_recording_seconds': u.max_recording_seconds,
            'created_by_username': u.created_by.username if u.created_by else None,
            'date_joined': u.date_joined.isoformat(),
        })
    return users


def _serialize_site_settings():
    ss = SiteSettings.load()
    return {'max_recordings_per_project': ss.max_recordings_per_project}


def _serialize_projects():
    projects = []
    for p in Project.objects.select_related('owner').all():
        projects.append({
            'id': str(p.id),
            'name': p.name,
            'owner_username': p.owner.username,
            'description': p.description,
            'created_at': p.created_at.isoformat(),
            'updated_at': p.updated_at.isoformat(),
        })
    return projects


def _serialize_videos():
    videos = []
    for v in Video.objects.all():
        videos.append({
            'id': str(v.id),
            'project_id': str(v.project_id),
            'file_path': v.file.name if v.file else '',
            'filename_original': v.filename_original,
            'duration_seconds': v.duration_seconds,
            'file_size_bytes': v.file_size_bytes,
            'elo_rating': v.elo_rating,
            'comparison_count': v.comparison_count,
            'thumbnail_path': v.thumbnail.name if v.thumbnail else '',
            'created_at': v.created_at.isoformat(),
        })
    return videos


def _serialize_comparisons():
    comparisons = []
    for c in Comparison.objects.select_related('user').all():
        comparisons.append({
            'id': str(c.id),
            'project_id': str(c.project_id),
            'video_left_id': str(c.video_left_id),
            'video_right_id': str(c.video_right_id),
            'result': c.result,
            'user_username': c.user.username,
            'created_at': c.created_at.isoformat(),
        })
    return comparisons


def _serialize_keybind_preferences():
    prefs = []
    for k in KeybindPreference.objects.select_related('user').all():
        prefs.append({
            'user_username': k.user.username,
            'start_stop_key': k.start_stop_key,
            'discard_key': k.discard_key,
        })
    return prefs


def _serialize_recording_settings():
    settings_list = []
    for r in RecordingSettings.objects.select_related('user').all():
        settings_list.append({
            'user_username': r.user.username,
            'video_resolution': r.video_resolution,
            'frame_rate': r.frame_rate,
            'video_codec': r.video_codec,
            'audio_enabled': r.audio_enabled,
            'audio_codec': r.audio_codec,
            'audio_bitrate': r.audio_bitrate,
        })
    return settings_list


def export_data_files():
    """Yield (arcname, bytes) tuples for each JSON data file."""
    manifest = {
        'export_version': EXPORT_VERSION,
        'created_at': timezone.now().isoformat(),
        'app': 'Video Production Manager',
    }
    yield ('manifest.json', json.dumps(manifest, indent=2).encode())
    yield ('data/users.json', json.dumps(_serialize_users(), indent=2).encode())
    yield ('data/site_settings.json', json.dumps(_serialize_site_settings(), indent=2).encode())
    yield ('data/projects.json', json.dumps(_serialize_projects(), indent=2).encode())
    yield ('data/videos.json', json.dumps(_serialize_videos(), indent=2).encode())
    yield ('data/comparisons.json', json.dumps(_serialize_comparisons(), indent=2).encode())
    yield ('data/keybind_preferences.json', json.dumps(_serialize_keybind_preferences(), indent=2).encode())
    yield ('data/recording_settings.json', json.dumps(_serialize_recording_settings(), indent=2).encode())


def collect_media_files():
    """Yield (arcname, absolute_path) for every video and thumbnail on disk."""
    for video in Video.objects.all():
        if video.file:
            abs_path = os.path.join(settings.MEDIA_ROOT, video.file.name)
            if os.path.isfile(abs_path):
                yield (f'media/{video.file.name}', abs_path)
        if video.thumbnail:
            abs_path = os.path.join(settings.MEDIA_ROOT, video.thumbnail.name)
            if os.path.isfile(abs_path):
                yield (f'media/{video.thumbnail.name}', abs_path)


class ExportStreamer:
    """Stream a zip archive containing both in-memory data and on-disk files."""

    CHUNK_SIZE = 64 * 1024

    def stream_export(self, json_entries, media_entries):
        """
        Yield bytes chunks forming a valid zip.

        json_entries: list of (arcname, bytes_data)
        media_entries: iterable of (arcname, file_path)
        """
        buffer = _StreamingBuffer()
        with zipfile.ZipFile(buffer, mode='w', compression=zipfile.ZIP_STORED) as zf:
            # Write JSON data files (small, in-memory)
            for arcname, data in json_entries:
                zinfo = zipfile.ZipInfo(arcname, date_time=time.localtime()[:6])
                zinfo.compress_type = zipfile.ZIP_STORED
                zf.writestr(zinfo, data)
                chunk = buffer.pop()
                if chunk:
                    yield chunk

            # Write media files (large, streamed from disk)
            for arcname, file_path in media_entries:
                if not os.path.isfile(file_path):
                    continue
                zinfo = zipfile.ZipInfo(arcname, date_time=time.localtime()[:6])
                zinfo.compress_type = zipfile.ZIP_STORED
                zinfo.file_size = os.path.getsize(file_path)
                with zf.open(zinfo, 'w') as dest:
                    with open(file_path, 'rb') as src:
                        while True:
                            chunk = src.read(self.CHUNK_SIZE)
                            if not chunk:
                                break
                            dest.write(chunk)
                            data = buffer.pop()
                            if data:
                                yield data

        data = buffer.pop()
        if data:
            yield data


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_from_zip(zip_path):
    """
    Import all data and media from an export zip.

    Returns a summary string. Raises ValueError on validation failures.
    """
    with zipfile.ZipFile(zip_path, 'r') as zf:
        # Validate manifest
        manifest = json.loads(zf.read('manifest.json'))
        if manifest.get('export_version', 0) > EXPORT_VERSION:
            raise ValueError(
                f"Export version {manifest['export_version']} is newer than "
                f"supported version {EXPORT_VERSION}."
            )

        users_data = json.loads(zf.read('data/users.json'))
        site_settings_data = json.loads(zf.read('data/site_settings.json'))
        projects_data = json.loads(zf.read('data/projects.json'))
        videos_data = json.loads(zf.read('data/videos.json'))
        comparisons_data = json.loads(zf.read('data/comparisons.json'))
        keybinds_data = json.loads(zf.read('data/keybind_preferences.json'))
        rec_settings_data = json.loads(zf.read('data/recording_settings.json'))

        stats = {}

        with transaction.atomic():
            username_map = _import_users_pass1(users_data)
            stats['users'] = len(users_data)

            _import_users_pass2(users_data, username_map)

            _import_site_settings(site_settings_data)

            _import_projects(projects_data, username_map)
            stats['projects'] = len(projects_data)

            _import_videos(videos_data)
            stats['videos'] = len(videos_data)

            _import_comparisons(comparisons_data, username_map)
            stats['comparisons'] = len(comparisons_data)

            _import_keybinds(keybinds_data, username_map)
            _import_recording_settings(rec_settings_data, username_map)

        # Extract media files outside transaction (filesystem ops)
        media_count = _extract_media(zf)
        stats['media_files'] = media_count

    parts = [f'{v} {k}' for k, v in stats.items()]
    return ', '.join(parts)


def _import_users_pass1(users_data):
    """Create user records, return {username: User} map."""
    username_map = {}
    for u in users_data:
        user, created = User.objects.get_or_create(
            username=u['username'],
            defaults={
                'email': u.get('email', ''),
                'is_staff': u.get('is_staff', False),
                'is_superuser': u.get('is_superuser', False),
                'is_active': u.get('is_active', True),
                'max_recording_seconds': u.get('max_recording_seconds', 300),
            },
        )
        if created:
            # Set password hash directly (not via set_password which re-hashes)
            user.password = u['password']
            user.save(update_fields=['password'])
            if u.get('date_joined'):
                User.objects.filter(pk=user.pk).update(
                    date_joined=parse_datetime(u['date_joined']),
                )
        username_map[u['username']] = user
    return username_map


def _import_users_pass2(users_data, username_map):
    """Set created_by FK for users."""
    for u in users_data:
        if u.get('created_by_username'):
            creator = username_map.get(u['created_by_username'])
            if creator:
                user = username_map[u['username']]
                if user.created_by_id != creator.pk:
                    user.created_by = creator
                    user.save(update_fields=['created_by'])


def _import_site_settings(data):
    ss = SiteSettings.load()
    ss.max_recordings_per_project = data.get('max_recordings_per_project', 0)
    ss.save()


def _import_projects(projects_data, username_map):
    for p in projects_data:
        owner = username_map.get(p['owner_username'])
        if not owner:
            continue
        proj, created = Project.objects.get_or_create(
            id=p['id'],
            defaults={
                'name': p['name'],
                'owner': owner,
                'description': p.get('description', ''),
            },
        )
        if created:
            updates = {}
            if p.get('created_at'):
                updates['created_at'] = parse_datetime(p['created_at'])
            if p.get('updated_at'):
                updates['updated_at'] = parse_datetime(p['updated_at'])
            if updates:
                Project.objects.filter(id=proj.id).update(**updates)


def _import_videos(videos_data):
    for v in videos_data:
        video, created = Video.objects.get_or_create(
            id=v['id'],
            defaults={
                'project_id': v['project_id'],
                'file': v.get('file_path', ''),
                'filename_original': v.get('filename_original', ''),
                'duration_seconds': v.get('duration_seconds'),
                'file_size_bytes': v.get('file_size_bytes', 0),
                'elo_rating': v.get('elo_rating', 1500.0),
                'comparison_count': v.get('comparison_count', 0),
                'thumbnail': v.get('thumbnail_path', ''),
            },
        )
        if created and v.get('created_at'):
            Video.objects.filter(id=video.id).update(
                created_at=parse_datetime(v['created_at']),
            )


def _import_comparisons(comparisons_data, username_map):
    for c in comparisons_data:
        user = username_map.get(c['user_username'])
        if not user:
            continue
        comp, created = Comparison.objects.get_or_create(
            id=c['id'],
            defaults={
                'project_id': c['project_id'],
                'video_left_id': c['video_left_id'],
                'video_right_id': c['video_right_id'],
                'result': c['result'],
                'user': user,
            },
        )
        if created and c.get('created_at'):
            Comparison.objects.filter(id=comp.id).update(
                created_at=parse_datetime(c['created_at']),
            )


def _import_keybinds(data, username_map):
    for k in data:
        user = username_map.get(k['user_username'])
        if user:
            KeybindPreference.objects.update_or_create(
                user=user,
                defaults={
                    'start_stop_key': k.get('start_stop_key', 'Space'),
                    'discard_key': k.get('discard_key', 'Escape'),
                },
            )


def _import_recording_settings(data, username_map):
    for r in data:
        user = username_map.get(r['user_username'])
        if user:
            RecordingSettings.objects.update_or_create(
                user=user,
                defaults={
                    'video_resolution': r.get('video_resolution', '1080p'),
                    'frame_rate': r.get('frame_rate', 30),
                    'video_codec': r.get('video_codec', 'vp9'),
                    'audio_enabled': r.get('audio_enabled', True),
                    'audio_codec': r.get('audio_codec', 'opus'),
                    'audio_bitrate': r.get('audio_bitrate', 192),
                },
            )


def _extract_media(zf):
    """Extract media/* entries from the zip into MEDIA_ROOT."""
    media_root = os.path.realpath(str(settings.MEDIA_ROOT))
    count = 0
    for name in zf.namelist():
        if not name.startswith('media/') or name.endswith('/'):
            continue
        rel_path = name[len('media/'):]
        dest = os.path.join(media_root, rel_path)
        # Path traversal protection
        if not os.path.realpath(dest).startswith(media_root):
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if not os.path.exists(dest):
            with zf.open(name) as src, open(dest, 'wb') as dst:
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
            count += 1
    return count
