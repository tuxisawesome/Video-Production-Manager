"""
Token-based authentication middleware for WebSocket connections.

Intercepts WebSocket connections on the phone recording path,
extracts the token from the URL, looks up the corresponding
RecordingSession, and populates scope['user'] and
scope['recording_session'].
"""

import re
from urllib.parse import urlparse

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

from recording.models import RecordingSession

# Pattern matching the phone WebSocket path.
PHONE_WS_PATTERN = re.compile(r'^/ws/recording/phone/(?P<token>[A-Za-z0-9_-]+)/$')


class TokenAuthMiddleware(BaseMiddleware):
    """
    Middleware that authenticates phone WebSocket connections via token.

    For phone paths, extracts the token from the URL, validates the
    corresponding RecordingSession, and sets:
        - scope['user'] to the session owner
        - scope['recording_session'] to the RecordingSession instance

    Non-phone paths pass through unmodified.
    """

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            path = scope.get('path', '')
            match = PHONE_WS_PATTERN.match(path)

            if match:
                token = match.group('token')
                session, user = await self._authenticate_token(token)

                if session is not None:
                    scope['user'] = user
                    scope['recording_session'] = session
                else:
                    scope['user'] = AnonymousUser()
                    scope['recording_session'] = None

        return await super().__call__(scope, receive, send)

    @database_sync_to_async
    def _authenticate_token(self, token):
        """
        Look up a RecordingSession by token.

        Returns:
            (session, user) if valid; (None, None) if invalid or expired.
        """
        try:
            session = RecordingSession.objects.select_related('user').get(
                token=token, is_active=True,
            )
        except RecordingSession.DoesNotExist:
            return None, None

        if session.expires_at < timezone.now():
            return None, None

        return session, session.user
