'use strict';

// ---------------------------------------------------------------------------
// Page data & DOM references
// ---------------------------------------------------------------------------

const pageData = JSON.parse(document.getElementById('page-data').textContent);
const csrfToken = pageData.csrf_token;

const dom = {
    qrCode:          document.querySelector('#qr-code'),
    connectionIcon:  document.querySelector('#connection-icon'),
    connectionStatus: document.querySelector('#connection-status'),
    recordingIcon:   document.querySelector('#recording-icon'),
    recordingStatus: document.querySelector('#recording-status'),
    recordingTimer:  document.querySelector('#recording-timer'),
    uploadProgressRow: document.querySelector('#upload-progress-row'),
    uploadProgressBar: document.querySelector('#upload-progress-bar'),
    recordingCount:  document.querySelector('#recording-count'),
    recordingMax:    document.querySelector('#recording-max'),
    settingsSummary: document.querySelector('#settings-summary'),
    keybindStartStop: document.querySelector('#keybind-start-stop'),
    keybindDiscard:  document.querySelector('#keybind-discard'),
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let ws = null;
let sessionId = null;
let isRecording = false;
let isPhoneConnected = false;
let recordingCount = 0;

// Timer state
let timerInterval = null;
let timerSeconds = 0;

// Reconnect state
let reconnectDelay = 1000;
const RECONNECT_MAX_DELAY = 30000;
let reconnectTimeout = null;

// Keepalive
let keepaliveInterval = null;

// Keybind & settings data (fetched from server)
let keybinds = { start_stop_key: 'Space', discard_key: 'Escape' };
let recordingSettings = {};

// ---------------------------------------------------------------------------
// CSRF helper
// ---------------------------------------------------------------------------

function csrfHeaders() {
    return {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
    };
}

// ---------------------------------------------------------------------------
// Timer
// ---------------------------------------------------------------------------

function formatTime(totalSeconds) {
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = totalSeconds % 60;
    return [h, m, s].map(v => String(v).padStart(2, '0')).join(':');
}

function startTimer() {
    stopTimer();
    timerSeconds = 0;
    dom.recordingTimer.textContent = '00:00:00';
    timerInterval = setInterval(() => {
        timerSeconds += 1;
        dom.recordingTimer.textContent = formatTime(timerSeconds);
    }, 1000);
}

function stopTimer() {
    if (timerInterval !== null) {
        clearInterval(timerInterval);
        timerInterval = null;
    }
}

function resetTimer() {
    stopTimer();
    timerSeconds = 0;
    dom.recordingTimer.textContent = '00:00';
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function setConnectionStatus(text, iconName, iconColor) {
    dom.connectionStatus.textContent = text;
    dom.connectionIcon.textContent = iconName || 'radio_button_unchecked';
    dom.connectionIcon.style.color = iconColor || '';
}

function setRecordingStatus(text, iconName, iconColor) {
    dom.recordingStatus.textContent = text;
    if (iconName) dom.recordingIcon.textContent = iconName;
    if (iconColor) {
        dom.recordingIcon.style.color = iconColor;
    } else {
        dom.recordingIcon.style.color = 'var(--md-sys-color-on-surface-variant)';
    }
}

function showUploadProgress(percent) {
    dom.uploadProgressRow.style.display = 'flex';
    dom.uploadProgressBar.style.width = `${percent}%`;
}

function hideUploadProgress() {
    dom.uploadProgressRow.style.display = 'none';
    dom.uploadProgressBar.style.width = '0%';
}

function updateRecordingCount() {
    dom.recordingCount.textContent = recordingCount;
}

function updateSettingsSummary(settings) {
    if (!settings) return;
    const parts = [];
    if (settings.video_resolution) parts.push(settings.video_resolution);
    if (settings.frame_rate) parts.push(`${settings.frame_rate} fps`);
    if (settings.video_codec) parts.push(settings.video_codec.toUpperCase());
    if (settings.audio_enabled) {
        const audioParts = [];
        if (settings.audio_codec) audioParts.push(settings.audio_codec.toUpperCase());
        if (settings.audio_bitrate) audioParts.push(`${settings.audio_bitrate} kbps`);
        parts.push(`Audio: ${audioParts.join(' ')}`);
    } else {
        parts.push('Audio: Off');
    }
    dom.settingsSummary.textContent = parts.join(' \u00b7 ');
}

function formatKeyName(code) {
    // Convert event.code values to readable labels for the UI.
    const map = {
        'Space': 'Space',
        'Escape': 'Escape',
        'Enter': 'Enter',
        'ShiftLeft': 'Left Shift',
        'ShiftRight': 'Right Shift',
        'ControlLeft': 'Left Ctrl',
        'ControlRight': 'Right Ctrl',
    };
    if (map[code]) return map[code];
    if (code.startsWith('Key')) return code.slice(3);
    if (code.startsWith('Digit')) return code.slice(5);
    return code;
}

function updateKeybindLabels() {
    if (dom.keybindStartStop) {
        dom.keybindStartStop.textContent = formatKeyName(keybinds.start_stop_key);
    }
    if (dom.keybindDiscard) {
        dom.keybindDiscard.textContent = formatKeyName(keybinds.discard_key);
    }
}

// ---------------------------------------------------------------------------
// QR code generation
// ---------------------------------------------------------------------------

function generateQRCode(url) {
    dom.qrCode.innerHTML = '';
    new QRCode(dom.qrCode, {
        text: url,
        width: 230,
        height: 230,
        colorDark: '#000000',
        colorLight: '#ffffff',
        correctLevel: QRCode.CorrectLevel.M,
    });
}

// ---------------------------------------------------------------------------
// WebSocket connection
// ---------------------------------------------------------------------------

function connectWebSocket() {
    if (!sessionId) return;

    const wsUrl = `${pageData.ws_scheme}://${pageData.domain}/ws/recording/control/${sessionId}/`;
    ws = new WebSocket(wsUrl);

    ws.addEventListener('open', onWsOpen);
    ws.addEventListener('close', onWsClose);
    ws.addEventListener('error', onWsError);
    ws.addEventListener('message', onWsMessage);
}

function onWsOpen() {
    console.log('[WS] Connected');
    reconnectDelay = 1000;
    setConnectionStatus('Connected. Waiting for phone...', 'wifi', 'var(--md-sys-color-primary)');
    startKeepalive();
}

function onWsClose(event) {
    console.log('[WS] Disconnected', event.code);
    stopKeepalive();
    isPhoneConnected = false;

    if (isRecording) {
        isRecording = false;
        stopTimer();
        setRecordingStatus('Not recording', 'stop_circle');
    }

    setConnectionStatus('Disconnected. Reconnecting...', 'wifi_off', 'var(--md-sys-color-error)');
    scheduleReconnect();
}

function onWsError(event) {
    console.error('[WS] Error', event);
}

function onWsMessage(event) {
    let data;
    try {
        data = JSON.parse(event.data);
    } catch {
        return;
    }

    const msgType = data.type;

    // Server-side keepalive ping; reply with pong.
    if (msgType === 'ping') {
        wsSend({ type: 'pong' });
        return;
    }

    // Status updates forwarded from the phone via the server consumer.
    if (msgType === 'status_update') {
        handleStatusUpdate(data.status, data.data || {});
        return;
    }
}

function handleStatusUpdate(status, data) {
    switch (status) {
        case 'phone_connected':
            isPhoneConnected = true;
            setConnectionStatus(
                'Phone connected. Ready to record.',
                'smartphone',
                'var(--md-sys-color-primary)',
            );
            break;

        case 'phone_disconnected':
            isPhoneConnected = false;
            setConnectionStatus(
                'Phone disconnected.',
                'smartphone',
                'var(--md-sys-color-error)',
            );
            if (isRecording) {
                isRecording = false;
                stopTimer();
                setRecordingStatus('Not recording', 'stop_circle');
            }
            break;

        case 'status_recording':
            isRecording = true;
            setRecordingStatus('Recording...', 'fiber_manual_record', 'var(--md-sys-color-error)');
            startTimer();
            hideUploadProgress();
            break;

        case 'status_stopped':
            isRecording = false;
            stopTimer();
            setRecordingStatus('Processing...', 'hourglass_top', 'var(--md-sys-color-tertiary)');
            break;

        case 'status_upload_progress': {
            const percent = data.percent || 0;
            showUploadProgress(percent);
            setRecordingStatus(`Uploading... ${Math.round(percent)}%`, 'cloud_upload', 'var(--md-sys-color-primary)');
            break;
        }

        case 'status_upload_complete':
            recordingCount += 1;
            updateRecordingCount();
            hideUploadProgress();
            setRecordingStatus('Upload complete. Ready for next recording.', 'check_circle', 'var(--md-sys-color-primary)');
            resetTimer();
            break;

        case 'status_discarded':
            isRecording = false;
            hideUploadProgress();
            setRecordingStatus('Recording discarded.', 'delete', 'var(--md-sys-color-on-surface-variant)');
            resetTimer();
            break;

        case 'error':
            setRecordingStatus(
                `Error: ${data.message || 'Unknown error'}`,
                'error',
                'var(--md-sys-color-error)',
            );
            break;

        case 'recording_count':
            recordingCount = data.count || 0;
            updateRecordingCount();
            if (data.max != null) {
                dom.recordingMax.textContent = data.max;
            }
            break;

        default:
            console.warn('[WS] Unknown status:', status, data);
    }
}

// ---------------------------------------------------------------------------
// WebSocket send helper
// ---------------------------------------------------------------------------

function wsSend(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(obj));
    }
}

// ---------------------------------------------------------------------------
// Keepalive
// ---------------------------------------------------------------------------

function startKeepalive() {
    stopKeepalive();
    keepaliveInterval = setInterval(() => {
        wsSend({ type: 'pong' });
    }, 30000);
}

function stopKeepalive() {
    if (keepaliveInterval !== null) {
        clearInterval(keepaliveInterval);
        keepaliveInterval = null;
    }
}

// ---------------------------------------------------------------------------
// Reconnect with exponential backoff
// ---------------------------------------------------------------------------

function scheduleReconnect() {
    if (reconnectTimeout) clearTimeout(reconnectTimeout);

    reconnectTimeout = setTimeout(() => {
        console.log(`[WS] Reconnecting (delay: ${reconnectDelay}ms)...`);
        connectWebSocket();
        reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_DELAY);
    }, reconnectDelay);
}

function cancelReconnect() {
    if (reconnectTimeout) {
        clearTimeout(reconnectTimeout);
        reconnectTimeout = null;
    }
}

// ---------------------------------------------------------------------------
// Keyboard controls
// ---------------------------------------------------------------------------

// Map event.code values to event.key equivalents for fallback matching.
const CODE_TO_KEY = {
    'Space': ' ',
    'Escape': 'Escape',
    'Enter': 'Enter',
    'ShiftLeft': 'Shift',
    'ShiftRight': 'Shift',
    'ControlLeft': 'Control',
    'ControlRight': 'Control',
};

function matchesKeybind(event, keybindCode) {
    if (event.code === keybindCode) return true;
    // Fallback: match by event.key for known keys.
    if (CODE_TO_KEY[keybindCode] && event.key === CODE_TO_KEY[keybindCode]) return true;
    // Fallback: letter keys ('KeyA' -> 'a').
    if (keybindCode.startsWith('Key') && event.key.toLowerCase() === keybindCode.slice(3).toLowerCase()) return true;
    // Fallback: digit keys ('Digit1' -> '1').
    if (keybindCode.startsWith('Digit') && event.key === keybindCode.slice(5)) return true;
    return false;
}

function onKeyDown(event) {
    // Ignore keybinds when focused on form elements.
    const tag = event.target.tagName.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || event.target.isContentEditable) {
        return;
    }

    if (!isPhoneConnected) return;

    if (matchesKeybind(event, keybinds.start_stop_key)) {
        event.preventDefault();
        if (isRecording) {
            wsSend({ type: 'stop_recording' });
        } else {
            wsSend({ type: 'start_recording', data: recordingSettings });
        }
        return;
    }

    if (matchesKeybind(event, keybinds.discard_key)) {
        event.preventDefault();
        if (isRecording) {
            wsSend({ type: 'discard_recording' });
        }
        return;
    }
}

// ---------------------------------------------------------------------------
// Fetch keybinds & settings from server
// ---------------------------------------------------------------------------

async function fetchKeybinds() {
    try {
        const res = await fetch(pageData.keybinds_url, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin',
        });
        if (res.ok) {
            keybinds = await res.json();
            updateKeybindLabels();
        }
    } catch (err) {
        console.error('[Keybinds] Fetch failed:', err);
    }
}

async function fetchRecordingSettings() {
    try {
        const res = await fetch(pageData.settings_url, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin',
        });
        if (res.ok) {
            recordingSettings = await res.json();
            updateSettingsSummary(recordingSettings);
        }
    } catch (err) {
        console.error('[Settings] Fetch failed:', err);
    }
}

// ---------------------------------------------------------------------------
// Session initialization
// ---------------------------------------------------------------------------

async function initSession() {
    try {
        const res = await fetch(pageData.start_session_url, {
            method: 'POST',
            headers: csrfHeaders(),
            credentials: 'same-origin',
        });

        if (!res.ok) {
            throw new Error(`Session start failed: ${res.status}`);
        }

        const data = await res.json();
        sessionId = data.session_id;

        // Generate QR code for the phone to scan.
        generateQRCode(data.qr_url);

        // Open WebSocket connection to the control channel.
        connectWebSocket();
    } catch (err) {
        console.error('[Session] Init failed:', err);
        setConnectionStatus('Failed to start session. Please reload.', 'error', 'var(--md-sys-color-error)');
    }
}

// ---------------------------------------------------------------------------
// Cleanup
// ---------------------------------------------------------------------------

function cleanup() {
    cancelReconnect();
    stopKeepalive();
    stopTimer();

    if (ws) {
        ws.removeEventListener('close', onWsClose);
        ws.close();
        ws = null;
    }
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

document.addEventListener('keydown', onKeyDown);

window.addEventListener('beforeunload', cleanup);

// Fetch preferences and start session in parallel.
fetchKeybinds();
fetchRecordingSettings();
initSession();
