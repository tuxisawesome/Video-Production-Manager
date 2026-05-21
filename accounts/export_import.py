"""
Export and import utilities for full-site data migration.

Produces a zip with JSON data + all media (videos and thumbnails).
The format is versioned via manifest.json's `export_version`.

History:
  v1 — original layout: Project owned Videos directly.
  v2 — current: Project -> Gallery -> Video, plus ProjectShare /
       GalleryShare / ShareLink / VideoComment / Video health fields.

The v2 importer accepts v1 exports too. When loading a v1 archive
we synthesize a single "Default" Gallery per Project and reroute the
old Video.project_id / Comparison.project_id references through it.
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
from projects.models import (
    Gallery,
    GalleryShare,
    Project,
    ProjectShare,
    ShareLink,
    Video,
    VideoComment,
)
from projects.utils import _StreamingBuffer
from recording.models import Comparison, KeybindPreference, RecordingSettings

User = get_user_model()

EXPORT_VERSION = 2


# ---------------------------------------------------------------------------
# Export serializers
# ---------------------------------------------------------------------------

def _serialize_users():
    out = []
    for u in User.objects.all():
        out.append({
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
    return out


def _serialize_site_settings():
    ss = SiteSettings.load()
    return {'max_recordings_per_project': ss.max_recordings_per_project}


def _serialize_projects():
    out = []
    for p in Project.objects.select_related('owner').all():
        out.append({
            'id': str(p.id),
            'name': p.name,
            'owner_username': p.owner.username,
            'description': p.description,
            'created_at': p.created_at.isoformat(),
            'updated_at': p.updated_at.isoformat(),
        })
    return out


def _serialize_galleries():
    out = []
    for g in Gallery.objects.all():
        out.append({
            'id': str(g.id),
            'project_id': str(g.project_id),
            'name': g.name,
            'description': g.description,
            'created_at': g.created_at.isoformat(),
            'updated_at': g.updated_at.isoformat(),
        })
    return out


def _serialize_videos():
    out = []
    for v in Video.objects.all():
        out.append({
            'id': str(v.id),
            'gallery_id': str(v.gallery_id),
            'file_path': v.file.name if v.file else '',
            'filename_original': v.filename_original,
            'duration_seconds': v.duration_seconds,
            'file_size_bytes': v.file_size_bytes,
            'elo_rating': v.elo_rating,
            'comparison_count': v.comparison_count,
            'thumbnail_path': v.thumbnail.name if v.thumbnail else '',
            'created_at': v.created_at.isoformat(),
            'health_status': v.health_status,
            'health_detail': v.health_detail,
            'health_checked_at': v.health_checked_at.isoformat() if v.health_checked_at else None,
        })
    return out


def _serialize_comparisons():
    out = []
    for c in Comparison.objects.select_related('user').all():
        out.append({
            'id': str(c.id),
            'gallery_id': str(c.gallery_id),
            'video_left_id': str(c.video_left_id),
            'video_right_id': str(c.video_right_id),
            'result': c.result,
            'user_username': c.user.username if c.user_id else None,
            'created_at': c.created_at.isoformat(),
        })
    return out


def _serialize_project_shares():
    out = []
    for s in ProjectShare.objects.select_related('shared_with').all():
        out.append({
            'project_id': str(s.project_id),
            'shared_with_username': s.shared_with.username,
            'role': s.role,
            'created_at': s.created_at.isoformat(),
        })
    return out


def _serialize_gallery_shares():
    out = []
    for s in GalleryShare.objects.select_related('shared_with').all():
        out.append({
            'gallery_id': str(s.gallery_id),
            'shared_with_username': s.shared_with.username,
            'role': s.role,
            'created_at': s.created_at.isoformat(),
        })
    return out


def _serialize_share_links():
    out = []
    for sl in ShareLink.objects.select_related('created_by').all():
        out.append({
            'token': str(sl.token),
            'access_type': sl.access_type,
            'project_id': str(sl.project_id) if sl.project_id else None,
            'gallery_id': str(sl.gallery_id) if sl.gallery_id else None,
            'video_id':   str(sl.video_id) if sl.video_id else None,
            'password_hash': sl.password_hash,
            'created_by_username': sl.created_by.username,
            'expires_at': sl.expires_at.isoformat() if sl.expires_at else None,
            'created_at': sl.created_at.isoformat(),
        })
    return out


def _serialize_video_comments():
    out = []
    for c in VideoComment.objects.select_related('author').all():
        out.append({
            'id': c.id,
            'video_id': str(c.video_id),
            'author_username': c.author.username if c.author_id else None,
            'guest_name': c.guest_name,
            'share_link_token': c.share_link_token,
            'text': c.text,
            'timestamp_seconds': c.timestamp_seconds,
            'created_at': c.created_at.isoformat(),
        })
    return out


def _serialize_keybind_preferences():
    out = []
    for k in KeybindPreference.objects.select_related('user').all():
        out.append({
            'user_username': k.user.username,
            'start_stop_key': k.start_stop_key,
            'discard_key': k.discard_key,
        })
    return out


def _serialize_recording_settings():
    out = []
    for r in RecordingSettings.objects.select_related('user').all():
        out.append({
            'user_username': r.user.username,
            'video_resolution': r.video_resolution,
            'frame_rate': r.frame_rate,
            'video_codec': r.video_codec,
            'audio_enabled': r.audio_enabled,
            'audio_codec': r.audio_codec,
            'audio_bitrate': r.audio_bitrate,
        })
    return out


def export_data_files():
    """Yield (arcname, bytes) tuples for each JSON data file."""
    manifest = {
        'export_version': EXPORT_VERSION,
        'created_at': timezone.now().isoformat(),
        'app': 'Video Production Manager',
    }
    yield ('manifest.json', json.dumps(manifest, indent=2).encode())
    yield ('data/users.json',              json.dumps(_serialize_users(),               indent=2).encode())
    yield ('data/site_settings.json',      json.dumps(_serialize_site_settings(),       indent=2).encode())
    yield ('data/projects.json',           json.dumps(_serialize_projects(),            indent=2).encode())
    yield ('data/galleries.json',          json.dumps(_serialize_galleries(),           indent=2).encode())
    yield ('data/videos.json',             json.dumps(_serialize_videos(),              indent=2).encode())
    yield ('data/comparisons.json',        json.dumps(_serialize_comparisons(),         indent=2).encode())
    yield ('data/project_shares.json',     json.dumps(_serialize_project_shares(),      indent=2).encode())
    yield ('data/gallery_shares.json',     json.dumps(_serialize_gallery_shares(),      indent=2).encode())
    yield ('data/share_links.json',        json.dumps(_serialize_share_links(),         indent=2).encode())
    yield ('data/video_comments.json',     json.dumps(_serialize_video_comments(),      indent=2).encode())
    yield ('data/keybind_preferences.json', json.dumps(_serialize_keybind_preferences(), indent=2).encode())
    yield ('data/recording_settings.json', json.dumps(_serialize_recording_settings(),  indent=2).encode())


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
        buffer = _StreamingBuffer()
        with zipfile.ZipFile(buffer, mode='w', compression=zipfile.ZIP_STORED) as zf:
            for arcname, data in json_entries:
                zinfo = zipfile.ZipInfo(arcname, date_time=time.localtime()[:6])
                zinfo.compress_type = zipfile.ZIP_STORED
                zf.writestr(zinfo, data)
                chunk = buffer.pop()
                if chunk:
                    yield chunk

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

def _read_json(zf, name, default=None):
    try:
        return json.loads(zf.read(name))
    except KeyError:
        return default if default is not None else []


def import_from_zip(zip_path):
    """
    Import all data and media from an export zip. Returns a summary string.
    Raises ValueError on validation failures. Accepts v1 and v2 archives.
    """
    with zipfile.ZipFile(zip_path, 'r') as zf:
        manifest = json.loads(zf.read('manifest.json'))
        version = int(manifest.get('export_version', 1))
        if version > EXPORT_VERSION:
            raise ValueError(
                f"Export version {version} is newer than supported version "
                f"{EXPORT_VERSION}. Upgrade the server first."
            )

        users_data         = _read_json(zf, 'data/users.json')
        site_settings_data = _read_json(zf, 'data/site_settings.json', default={})
        projects_data      = _read_json(zf, 'data/projects.json')
        galleries_data     = _read_json(zf, 'data/galleries.json')
        videos_data        = _read_json(zf, 'data/videos.json')
        comparisons_data   = _read_json(zf, 'data/comparisons.json')
        project_shares     = _read_json(zf, 'data/project_shares.json')
        gallery_shares     = _read_json(zf, 'data/gallery_shares.json')
        share_links_data   = _read_json(zf, 'data/share_links.json')
        comments_data      = _read_json(zf, 'data/video_comments.json')
        keybinds_data      = _read_json(zf, 'data/keybind_preferences.json')
        rec_settings_data  = _read_json(zf, 'data/recording_settings.json')

        stats = {}

        with transaction.atomic():
            username_map = _import_users_pass1(users_data)
            stats['users'] = len(users_data)

            _import_users_pass2(users_data, username_map)
            _import_site_settings(site_settings_data)

            _import_projects(projects_data, username_map)
            stats['projects'] = len(projects_data)

            # v1 archives don't carry galleries — synthesize one per project so
            # videos and comparisons land somewhere valid.
            if version < 2:
                galleries_data, project_to_default_gallery = _synthesize_default_galleries(projects_data)
                videos_data      = _v1_videos_to_v2(videos_data, project_to_default_gallery)
                comparisons_data = _v1_comparisons_to_v2(comparisons_data, project_to_default_gallery)

            _import_galleries(galleries_data)
            stats['galleries'] = len(galleries_data)

            _import_videos(videos_data)
            stats['videos'] = len(videos_data)

            _import_comparisons(comparisons_data, username_map)
            stats['comparisons'] = len(comparisons_data)

            _import_project_shares(project_shares, username_map)
            _import_gallery_shares(gallery_shares, username_map)
            stats['user_shares'] = len(project_shares) + len(gallery_shares)

            _import_share_links(share_links_data, username_map)
            stats['share_links'] = len(share_links_data)

            _import_video_comments(comments_data, username_map)
            stats['comments'] = len(comments_data)

            _import_keybinds(keybinds_data, username_map)
            _import_recording_settings(rec_settings_data, username_map)

        # Filesystem ops live outside the transaction.
        media_count = _extract_media(zf)
        stats['media_files'] = media_count

    return ', '.join(f'{v} {k}' for k, v in stats.items())


# --- pass-by-pass importers -----------------------------------------------

def _import_users_pass1(users_data):
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
            # Use stored hash directly (set_password would re-hash).
            user.password = u['password']
            user.save(update_fields=['password'])
            if u.get('date_joined'):
                User.objects.filter(pk=user.pk).update(date_joined=parse_datetime(u['date_joined']))
        username_map[u['username']] = user
    return username_map


def _import_users_pass2(users_data, username_map):
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
            if p.get('created_at'): updates['created_at'] = parse_datetime(p['created_at'])
            if p.get('updated_at'): updates['updated_at'] = parse_datetime(p['updated_at'])
            if updates:
                Project.objects.filter(id=proj.id).update(**updates)


def _import_galleries(galleries_data):
    for g in galleries_data:
        gal, created = Gallery.objects.get_or_create(
            id=g['id'],
            defaults={
                'project_id': g['project_id'],
                'name': g.get('name', 'Untitled'),
                'description': g.get('description', ''),
            },
        )
        if created:
            updates = {}
            if g.get('created_at'): updates['created_at'] = parse_datetime(g['created_at'])
            if g.get('updated_at'): updates['updated_at'] = parse_datetime(g['updated_at'])
            if updates:
                Gallery.objects.filter(id=gal.id).update(**updates)


def _import_videos(videos_data):
    for v in videos_data:
        video, created = Video.objects.get_or_create(
            id=v['id'],
            defaults={
                'gallery_id': v['gallery_id'],
                'file': v.get('file_path', ''),
                'filename_original': v.get('filename_original', ''),
                'duration_seconds': v.get('duration_seconds'),
                'file_size_bytes': v.get('file_size_bytes', 0),
                'elo_rating': v.get('elo_rating', 1500.0),
                'comparison_count': v.get('comparison_count', 0),
                'thumbnail': v.get('thumbnail_path', ''),
                'health_status': v.get('health_status', Video.HEALTH_UNKNOWN),
                'health_detail': v.get('health_detail', ''),
            },
        )
        if created:
            updates = {}
            if v.get('created_at'):
                updates['created_at'] = parse_datetime(v['created_at'])
            if v.get('health_checked_at'):
                updates['health_checked_at'] = parse_datetime(v['health_checked_at'])
            if updates:
                Video.objects.filter(id=video.id).update(**updates)


def _import_comparisons(comparisons_data, username_map):
    for c in comparisons_data:
        user = username_map.get(c.get('user_username')) if c.get('user_username') else None
        comp, created = Comparison.objects.get_or_create(
            id=c['id'],
            defaults={
                'gallery_id': c['gallery_id'],
                'video_left_id': c['video_left_id'],
                'video_right_id': c['video_right_id'],
                'result': c['result'],
                'user': user,
            },
        )
        if created and c.get('created_at'):
            Comparison.objects.filter(id=comp.id).update(created_at=parse_datetime(c['created_at']))


def _import_project_shares(rows, username_map):
    for s in rows:
        shared_with = username_map.get(s['shared_with_username'])
        if not shared_with:
            continue
        ProjectShare.objects.update_or_create(
            project_id=s['project_id'],
            shared_with=shared_with,
            defaults={'role': s.get('role', 'view')},
        )


def _import_gallery_shares(rows, username_map):
    for s in rows:
        shared_with = username_map.get(s['shared_with_username'])
        if not shared_with:
            continue
        GalleryShare.objects.update_or_create(
            gallery_id=s['gallery_id'],
            shared_with=shared_with,
            defaults={'role': s.get('role', 'view')},
        )


def _import_share_links(rows, username_map):
    for sl in rows:
        creator = username_map.get(sl['created_by_username'])
        if not creator:
            continue
        link, created = ShareLink.objects.get_or_create(
            token=sl['token'],
            defaults={
                'access_type': sl.get('access_type', ShareLink.VIEW),
                'project_id': sl.get('project_id'),
                'gallery_id': sl.get('gallery_id'),
                'video_id':   sl.get('video_id'),
                'password_hash': sl.get('password_hash', ''),
                'created_by': creator,
                'expires_at': parse_datetime(sl['expires_at']) if sl.get('expires_at') else None,
            },
        )
        if created and sl.get('created_at'):
            ShareLink.objects.filter(pk=link.pk).update(created_at=parse_datetime(sl['created_at']))


def _import_video_comments(rows, username_map):
    for c in rows:
        author = username_map.get(c.get('author_username')) if c.get('author_username') else None
        # VideoComment.id is auto BigAutoField — match by (video_id, author/guest, text, created_at)
        # is too fragile; just create a new row and let new IDs be assigned. If the same export
        # is imported twice, comments will duplicate — document that behavior.
        created_at = parse_datetime(c['created_at']) if c.get('created_at') else timezone.now()
        # Idempotency check by content + timestamp + video.
        existing_qs = VideoComment.objects.filter(
            video_id=c['video_id'],
            text=c.get('text', ''),
            created_at=created_at,
        )
        if existing_qs.exists():
            continue
        comment = VideoComment.objects.create(
            video_id=c['video_id'],
            author=author,
            guest_name=c.get('guest_name', '') or '',
            share_link_token=c.get('share_link_token', '') or '',
            text=c.get('text', ''),
            timestamp_seconds=c.get('timestamp_seconds'),
        )
        VideoComment.objects.filter(pk=comment.pk).update(created_at=created_at)


def _import_keybinds(rows, username_map):
    for k in rows:
        user = username_map.get(k['user_username'])
        if user:
            KeybindPreference.objects.update_or_create(
                user=user,
                defaults={
                    'start_stop_key': k.get('start_stop_key', 'Space'),
                    'discard_key': k.get('discard_key', 'Escape'),
                },
            )


def _import_recording_settings(rows, username_map):
    for r in rows:
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


# --- v1 → v2 translation --------------------------------------------------

def _synthesize_default_galleries(projects_data):
    """
    v1 → v2: build one Gallery per Project so v1 videos and comparisons
    have a gallery_id to live under. Returns (gallery_rows, {project_id: gallery_id}).
    """
    import uuid as _uuid
    galleries = []
    project_to_gallery = {}
    for p in projects_data:
        gallery_id = str(_uuid.uuid4())
        project_to_gallery[p['id']] = gallery_id
        galleries.append({
            'id': gallery_id,
            'project_id': p['id'],
            'name': 'Default',
            'description': 'Imported from a v1 archive.',
            'created_at': p.get('created_at'),
            'updated_at': p.get('updated_at'),
        })
    return galleries, project_to_gallery


def _v1_videos_to_v2(videos_data, project_to_gallery):
    out = []
    for v in videos_data:
        new = dict(v)
        new['gallery_id'] = project_to_gallery.get(v.get('project_id'))
        out.append(new)
    return out


def _v1_comparisons_to_v2(comparisons_data, project_to_gallery):
    out = []
    for c in comparisons_data:
        new = dict(c)
        new['gallery_id'] = project_to_gallery.get(c.get('project_id'))
        out.append(new)
    return out


# --- media extraction -----------------------------------------------------

def _extract_media(zf):
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
