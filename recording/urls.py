from django.urls import path

from recording import views

app_name = 'recording'

urlpatterns = [
    # Session management
    path(
        'session/start/<uuid:project_id>/',
        views.start_session,
        name='start_session',
    ),

    # Phone recording (token-authenticated)
    path('phone/<str:token>/', views.phone_recorder, name='phone_recorder'),
    path(
        'phone/<str:token>/chunk/',
        views.phone_chunk_upload,
        name='phone_chunk_upload',
    ),
    path(
        'phone/<str:token>/finalize/',
        views.phone_finalize,
        name='phone_finalize',
    ),
    path(
        'phone/<str:token>/discard/',
        views.phone_discard,
        name='phone_discard',
    ),

    # Ranking
    path('rank/<uuid:project_id>/', views.rank_view, name='rank_view'),
    path(
        'rank/<uuid:project_id>/next-pair/',
        views.next_pair,
        name='next_pair',
    ),
    path(
        'rank/<uuid:project_id>/submit/',
        views.submit_comparison,
        name='submit_comparison',
    ),

    # User preferences
    path('keybinds/', views.keybind_view, name='keybind_view'),
    path('settings/', views.recording_settings_view, name='recording_settings_view'),

    # Desktop recording control
    path(
        'control/<uuid:project_id>/',
        views.record_control,
        name='record_control',
    ),
]
