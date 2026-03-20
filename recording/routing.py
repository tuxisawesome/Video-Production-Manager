from django.urls import re_path

from recording import consumers

websocket_urlpatterns = [
    re_path(
        r'ws/recording/control/(?P<session_id>[0-9a-f-]+)/$',
        consumers.RecordingControlConsumer.as_asgi(),
    ),
    re_path(
        r'ws/recording/phone/(?P<token>[A-Za-z0-9_-]+)/$',
        consumers.PhoneRecordingConsumer.as_asgi(),
    ),
]
