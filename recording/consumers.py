import asyncio
import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from recording.models import RecordingSession


class RecordingControlConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for the desktop recording control page.

    Joins a channel group keyed by session_id so that messages from
    the phone consumer are relayed here and vice-versa.
    """

    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.group_name = f'recording_{self.session_id}'

        # Validate that the session exists and the connected user owns it.
        user = self.scope.get('user')
        if user is None or user.is_anonymous:
            await self.close()
            return

        session = await self._get_session()
        if session is None or session.user_id != user.id:
            await self.close()
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # Start keepalive ping loop.
        self._keepalive_task = asyncio.ensure_future(self._keepalive())

    async def disconnect(self, close_code):
        if hasattr(self, '_keepalive_task'):
            self._keepalive_task.cancel()
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(
                self.group_name, self.channel_name,
            )

    async def receive(self, text_data=None, bytes_data=None):
        """Handle messages from the desktop client."""
        if text_data is None:
            return

        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        msg_type = data.get('type')

        if msg_type == 'pong':
            return

        # Relay recording commands to the group (phone will pick them up).
        if msg_type in ('start_recording', 'stop_recording', 'discard_recording'):
            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'relay_command',
                    'command': msg_type,
                    'data': data.get('data', {}),
                    'sender_channel': self.channel_name,
                },
            )

    async def relay_command(self, event):
        """Receive relay_command from group -- ignore if we sent it."""
        if event.get('sender_channel') == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            'type': event['command'],
            'data': event.get('data', {}),
        }))

    async def status_update(self, event):
        """Forward phone status updates to the desktop client."""
        await self.send(text_data=json.dumps({
            'type': 'status_update',
            'status': event.get('status'),
            'data': event.get('data', {}),
        }))

    async def _keepalive(self):
        """Send a ping every 30 seconds to keep the connection alive."""
        try:
            while True:
                await asyncio.sleep(30)
                await self.send(text_data=json.dumps({'type': 'ping'}))
        except asyncio.CancelledError:
            pass

    @database_sync_to_async
    def _get_session(self):
        try:
            return RecordingSession.objects.get(
                pk=self.session_id, is_active=True,
            )
        except RecordingSession.DoesNotExist:
            return None


class PhoneRecordingConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for the phone recording page.

    Connects using a session token instead of Django auth.
    Relays status messages to the desktop consumer and receives
    commands from it.
    """

    async def connect(self):
        self.token = self.scope['url_route']['kwargs']['token']

        session = await self._get_session_by_token()
        if session is None:
            await self.close()
            return

        self.session_id = str(session.id)
        self.group_name = f'recording_{self.session_id}'

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # Notify the desktop that the phone has connected.
        await self.channel_layer.group_send(
            self.group_name,
            {
                'type': 'status_update',
                'status': 'phone_connected',
                'data': {},
                'sender_channel': self.channel_name,
            },
        )

        # Start keepalive ping loop.
        self._keepalive_task = asyncio.ensure_future(self._keepalive())

    async def disconnect(self, close_code):
        if hasattr(self, '_keepalive_task'):
            self._keepalive_task.cancel()
        if hasattr(self, 'group_name'):
            # Notify desktop that the phone disconnected.
            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'status_update',
                    'status': 'phone_disconnected',
                    'data': {},
                    'sender_channel': self.channel_name,
                },
            )
            await self.channel_layer.group_discard(
                self.group_name, self.channel_name,
            )

    async def receive(self, text_data=None, bytes_data=None):
        """Handle messages from the phone client."""
        if text_data is None:
            return

        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        msg_type = data.get('type')

        if msg_type == 'pong':
            return

        # Forward status messages from the phone to the desktop.
        if msg_type == 'status_update':
            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'status_update',
                    'status': data.get('status'),
                    'data': data.get('data', {}),
                    'sender_channel': self.channel_name,
                },
            )

    async def relay_command(self, event):
        """Forward commands from the desktop to the phone."""
        if event.get('sender_channel') == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            'type': event['command'],
            'data': event.get('data', {}),
        }))

    async def status_update(self, event):
        """Ignore status_update events that we ourselves sent."""
        if event.get('sender_channel') == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            'type': 'status_update',
            'status': event.get('status'),
            'data': event.get('data', {}),
        }))

    async def _keepalive(self):
        """Send a ping every 30 seconds to keep the connection alive."""
        try:
            while True:
                await asyncio.sleep(30)
                await self.send(text_data=json.dumps({'type': 'ping'}))
        except asyncio.CancelledError:
            pass

    @database_sync_to_async
    def _get_session_by_token(self):
        from django.utils import timezone

        try:
            session = RecordingSession.objects.get(
                token=self.token, is_active=True,
            )
        except RecordingSession.DoesNotExist:
            return None
        if session.expires_at < timezone.now():
            return None
        return session
