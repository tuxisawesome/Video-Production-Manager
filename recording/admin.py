from django.contrib import admin

from recording.models import (
    Comparison,
    KeybindPreference,
    RecordingSession,
    RecordingSettings,
)


@admin.register(RecordingSession)
class RecordingSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'project', 'user', 'is_active', 'created_at', 'expires_at')
    list_filter = ('is_active',)
    search_fields = ('token', 'user__username', 'project__name')
    readonly_fields = ('id', 'token', 'created_at')


@admin.register(Comparison)
class ComparisonAdmin(admin.ModelAdmin):
    list_display = ('id', 'project', 'video_left', 'video_right', 'result', 'user', 'created_at')
    list_filter = ('result',)
    search_fields = ('project__name', 'user__username')
    readonly_fields = ('id', 'created_at')


@admin.register(KeybindPreference)
class KeybindPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'start_stop_key', 'discard_key')
    search_fields = ('user__username',)


@admin.register(RecordingSettings)
class RecordingSettingsAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'video_resolution', 'frame_rate', 'video_codec',
        'audio_enabled', 'audio_codec', 'audio_bitrate',
    )
    search_fields = ('user__username',)
