'use strict';

// ---------------------------------------------------------------------------
// Page data & DOM references
// ---------------------------------------------------------------------------

const pageData = JSON.parse(document.getElementById('page-data').textContent);

const config = {
    token:      pageData.token,
    settings:   pageData.recording_settings,
    wsUrl:      pageData.ws_url,
    chunkUrl:   pageData.chunk_upload_url,
    finalizeUrl: pageData.finalize_url,
    discardUrl: pageData.discard_url,
};

const dom = {
    viewfinder:          document.getElementById('viewfinder'),
    connectionIndicator: document.getElementById('connection-indicator'),
    connectionText:      document.getElementById('connection-text'),
    statusChip:          document.getElementById('status-chip'),
    statusText:          document.getElementById('status-text'),
    recordingTimer:      document.getElementById('recording-timer'),
    timerText:           document.getElementById('timer-text'),
    uploadBar:           document.getElementById('upload-bar'),
    uploadBarFill:       document.getElementById('upload-bar-fill'),
};

// ---------------------------------------------------------------------------
// Resolution & bitrate maps
// ---------------------------------------------------------------------------

const RESOLUTION_MAP = {
    '4k':    { width: 3840, height: 2160 },
    '1080p': { width: 1920, height: 1080 },
    '720p':  { width: 1280, height: 720 },
    '480p':  { width: 640,  height: 480 },
};

const VIDEO_BITRATE_MAP = {
    '4k':    20_000_000,
    '1080p':  8_000_000,
    '720p':   5_000_000,
    '480p':   2_500_000,
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let ws = null;
let mediaStream = null;
let mediaRecorder = null;
let isRecording = false;

// Chunk upload state
let chunkQueue = [];
let isUploading = false;
let chunkIndex = 0;
let totalBytesUploaded = 0;
let totalBytesQueued = 0;
let allChunksQueued = false;   // true once MediaRecorder fires its final chunk

// Timer state
let timerInterval = null;
let timerSeconds = 0;

// Wake lock state
let wakeLock = null;

// WebSocket reconnect state
let reconnectDelay = 1000;
const RECONNECT_MAX_DELAY = 30000;
let reconnectTimeout = null;
let intentionalClose = false;

// Keepalive
let keepaliveInterval = null;

// ---------------------------------------------------------------------------
// Logging helper
// ---------------------------------------------------------------------------

function log(tag, ...args) {
    console.log(`[PhoneRecorder][${tag}]`, ...args);
}

function logError(tag, ...args) {
    console.error(`[PhoneRecorder][${tag}]`, ...args);
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function setConnectionUI(state, text) {
    dom.connectionText.textContent = text;
    dom.connectionIndicator.classList.remove('connected', 'disconnected');
    if (state === 'connected') {
        dom.connectionIndicator.classList.add('connected');
    } else if (state === 'disconnected') {
        dom.connectionIndicator.classList.add('disconnected');
    }
}

function setStatusText(text, chipClass) {
    dom.statusText.textContent = text;
    dom.statusChip.classList.remove('recording', 'uploading');
    if (chipClass) {
        dom.statusChip.classList.add(chipClass);
    }
}

function showUploadProgress(percent) {
    dom.uploadBar.classList.add('active');
    dom.uploadBarFill.style.width = `${Math.min(100, Math.max(0, percent))}%`;
}

function hideUploadProgress() {
    dom.uploadBar.classList.remove('active');
    dom.uploadBarFill.style.width = '0%';
}

function showRecordingTimer() {
    dom.recordingTimer.classList.add('active');
}

function hideRecordingTimer() {
    dom.recordingTimer.classList.remove('active');
}

// ---------------------------------------------------------------------------
// Timer
// ---------------------------------------------------------------------------

function formatTime(totalSeconds) {
    const m = Math.floor(totalSeconds / 60);
    const s = totalSeconds % 60;
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}

function startTimer() {
    stopTimer();
    timerSeconds = 0;
    dom.timerText.textContent = '00:00';
    showRecordingTimer();
    timerInterval = setInterval(() => {
        timerSeconds += 1;
        dom.timerText.textContent = formatTime(timerSeconds);
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
    dom.timerText.textContent = '00:00';
    hideRecordingTimer();
}

// ---------------------------------------------------------------------------
// Wake Lock
// ---------------------------------------------------------------------------

async function requestWakeLock() {
    if (!('wakeLock' in navigator)) return;
    try {
        wakeLock = await navigator.wakeLock.request('screen');
        wakeLock.addEventListener('release', () => {
            log('WakeLock', 'Released');
            wakeLock = null;
        });
        log('WakeLock', 'Acquired');
    } catch (err) {
        log('WakeLock', 'Request failed:', err.message);
    }
}

async function releaseWakeLock() {
    if (wakeLock) {
        try {
            await wakeLock.release();
        } catch (_) {
            // Already released
        }
        wakeLock = null;
    }
}

function onVisibilityChange() {
    if (document.visibilityState === 'visible' && isRecording && !wakeLock) {
        requestWakeLock();
    }
}

document.addEventListener('visibilitychange', onVisibilityChange);

// ---------------------------------------------------------------------------
// Camera initialization
// ---------------------------------------------------------------------------

async function initCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        setStatusText('Camera not supported');
        setConnectionUI('disconnected', 'Unsupported browser');
        wsSend({ type: 'status_update', status: 'error', data: { message: 'getUserMedia not available on this browser' } });
        return false;
    }

    const resolution = RESOLUTION_MAP[config.settings.video_resolution] || RESOLUTION_MAP['1080p'];
    const frameRate = config.settings.frame_rate || 30;

    const constraints = {
        video: {
            width:      { ideal: resolution.width },
            height:     { ideal: resolution.height },
            frameRate:  { ideal: frameRate },
            facingMode: { ideal: 'environment' },
        },
        audio: config.settings.audio_enabled === true,
    };

    try {
        mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
        dom.viewfinder.srcObject = mediaStream;
        log('Camera', 'Stream acquired');
        setStatusText('Waiting');
        return true;
    } catch (err) {
        logError('Camera', err);

        let userMsg = 'Camera access failed';
        if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
            userMsg = 'Camera permission denied';
        } else if (err.name === 'NotFoundError') {
            userMsg = 'No camera found';
        } else if (err.name === 'NotReadableError' || err.name === 'AbortError') {
            userMsg = 'Camera in use by another app';
        } else if (err.name === 'OverconstrainedError') {
            userMsg = 'Camera does not support requested settings';
        }

        setStatusText(userMsg);
        setConnectionUI('disconnected', 'Camera error');
        wsSend({ type: 'status_update', status: 'error', data: { message: userMsg } });
        return false;
    }
}

// ---------------------------------------------------------------------------
// MediaRecorder MIME type selection
// ---------------------------------------------------------------------------

function selectMimeType() {
    const vc = config.settings.video_codec || 'vp8';
    const ac = config.settings.audio_codec || 'opus';
    const hasAudio = config.settings.audio_enabled === true;

    // Build a prioritized list of MIME types to try.
    const candidates = [];

    if (hasAudio) {
        candidates.push(`video/webm;codecs=${vc},${ac}`);
        candidates.push(`video/webm;codecs=${vc}`);
    } else {
        candidates.push(`video/webm;codecs=${vc}`);
    }

    // Generic fallbacks
    candidates.push('video/webm;codecs=vp9,opus');
    candidates.push('video/webm;codecs=vp8,opus');
    candidates.push('video/webm;codecs=vp9');
    candidates.push('video/webm;codecs=vp8');
    candidates.push('video/webm');

    // MP4 fallbacks (Safari iOS)
    candidates.push('video/mp4;codecs=h264,aac');
    candidates.push('video/mp4;codecs=h264');
    candidates.push('video/mp4');

    for (const mime of candidates) {
        if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(mime)) {
            log('MIME', `Selected: ${mime}`);
            return mime;
        }
    }

    log('MIME', 'No supported type found, using browser default');
    return '';
}

// ---------------------------------------------------------------------------
// MediaRecorder lifecycle
// ---------------------------------------------------------------------------

function createMediaRecorder() {
    if (!mediaStream) return null;

    const mimeType = selectMimeType();
    const resolution = config.settings.video_resolution || '1080p';
    const videoBps = VIDEO_BITRATE_MAP[resolution] || VIDEO_BITRATE_MAP['1080p'];
    const audioBps = config.settings.audio_enabled
        ? (config.settings.audio_bitrate || 128) * 1000
        : undefined;

    const options = {};
    if (mimeType) options.mimeType = mimeType;
    options.videoBitsPerSecond = videoBps;
    if (audioBps) options.audioBitsPerSecond = audioBps;

    try {
        const recorder = new MediaRecorder(mediaStream, options);

        recorder.ondataavailable = onDataAvailable;
        recorder.onstop = onRecorderStop;
        recorder.onerror = onRecorderError;

        log('Recorder', `Created with mimeType=${recorder.mimeType}, videoBps=${videoBps}`);
        return recorder;
    } catch (err) {
        logError('Recorder', 'Failed to create MediaRecorder:', err);
        wsSend({ type: 'status_update', status: 'error', data: { message: `MediaRecorder creation failed: ${err.message}` } });
        return null;
    }
}

function onDataAvailable(event) {
    if (event.data && event.data.size > 0) {
        totalBytesQueued += event.data.size;
        chunkQueue.push(event.data);
        processChunkQueue();
    }
}

function onRecorderStop() {
    log('Recorder', 'Stopped');
    allChunksQueued = true;
    // Process any remaining chunks. Finalize will happen once queue drains.
    processChunkQueue();
}

function onRecorderError(event) {
    logError('Recorder', 'Error:', event.error);
    wsSend({ type: 'status_update', status: 'error', data: { message: `Recording error: ${event.error ? event.error.message : 'unknown'}` } });
}

// ---------------------------------------------------------------------------
// Chunk upload queue
// ---------------------------------------------------------------------------

async function processChunkQueue() {
    if (isUploading) return;
    if (chunkQueue.length === 0) {
        // If all chunks from MediaRecorder have been queued and queue is empty,
        // either finalize or discard depending on state.
        if (allChunksQueued && !isRecording) {
            await finalizeRecording();
        }
        return;
    }

    isUploading = true;
    const blob = chunkQueue.shift();
    const currentIndex = chunkIndex;
    chunkIndex += 1;

    try {
        const formData = new FormData();
        formData.append('chunk', blob, `chunk_${currentIndex}`);
        formData.append('chunk_index', String(currentIndex));

        const response = await fetch(config.chunkUrl, {
            method: 'POST',
            body: formData,
        });

        if (!response.ok) {
            throw new Error(`Chunk upload failed: ${response.status}`);
        }

        totalBytesUploaded += blob.size;

        // Report upload progress via WebSocket periodically.
        const percent = totalBytesQueued > 0
            ? Math.round((totalBytesUploaded / totalBytesQueued) * 100)
            : 0;
        wsSend({ type: 'status_update', status: 'status_upload_progress', data: { percent: percent } });
        showUploadProgress(percent);

        log('Upload', `Chunk ${currentIndex} uploaded (${blob.size} bytes), progress=${percent}%`);
    } catch (err) {
        logError('Upload', `Chunk ${currentIndex} failed:`, err);
        // Re-queue the chunk at the front for retry.
        chunkQueue.unshift(blob);
        chunkIndex -= 1;

        // Wait a moment before retrying.
        await new Promise(resolve => setTimeout(resolve, 2000));
    }

    isUploading = false;
    // Continue processing the queue.
    processChunkQueue();
}

// ---------------------------------------------------------------------------
// Finalize & discard
// ---------------------------------------------------------------------------

async function finalizeRecording() {
    if (!allChunksQueued) return;

    setStatusText('Uploading...', 'uploading');
    showUploadProgress(100);

    try {
        const response = await fetch(config.finalizeUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });

        if (!response.ok) {
            throw new Error(`Finalize failed: ${response.status}`);
        }

        const data = await response.json();
        const videoId = data.video_id;

        wsSend({ type: 'status_update', status: 'status_upload_complete', data: { video_id: videoId } });
        log('Finalize', `Complete. video_id=${videoId}`);

        setStatusText('Upload complete');
        hideUploadProgress();
        resetRecordingState();
    } catch (err) {
        logError('Finalize', err);
        wsSend({ type: 'status_update', status: 'error', data: { message: `Finalize failed: ${err.message}` } });
        setStatusText('Upload failed');
    }
}

async function discardRecording() {
    log('Discard', 'Discarding recording...');

    // Stop MediaRecorder if still active.
    stopMediaRecorder();

    // Clear the chunk queue immediately.
    chunkQueue = [];
    allChunksQueued = false;

    try {
        await fetch(config.discardUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
    } catch (err) {
        logError('Discard', err);
    }

    wsSend({ type: 'status_update', status: 'status_discarded', data: {} });

    isRecording = false;
    resetTimer();
    hideUploadProgress();
    setStatusText('Discarded');
    releaseWakeLock();
    resetRecordingState();
}

function resetRecordingState() {
    chunkIndex = 0;
    totalBytesUploaded = 0;
    totalBytesQueued = 0;
    allChunksQueued = false;
    chunkQueue = [];
    isUploading = false;
    mediaRecorder = null;
}

function stopMediaRecorder() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        try {
            mediaRecorder.stop();
        } catch (_) {
            // May already be stopped.
        }
    }
}

// ---------------------------------------------------------------------------
// Command handlers
// ---------------------------------------------------------------------------

function handleStartCommand() {
    if (isRecording) {
        log('Command', 'Ignoring start: already recording');
        return;
    }

    if (!mediaStream) {
        wsSend({ type: 'status_update', status: 'error', data: { message: 'Camera not initialized' } });
        return;
    }

    // Reset upload state for a new recording.
    resetRecordingState();

    mediaRecorder = createMediaRecorder();
    if (!mediaRecorder) return;

    isRecording = true;
    allChunksQueued = false;

    try {
        mediaRecorder.start(5000); // 5-second timeslice
    } catch (err) {
        logError('Recorder', 'start() failed:', err);
        wsSend({ type: 'status_update', status: 'error', data: { message: `Failed to start recording: ${err.message}` } });
        isRecording = false;
        return;
    }

    wsSend({ type: 'status_update', status: 'status_recording', data: {} });
    log('Command', 'Recording started');

    startTimer();
    setStatusText('Recording', 'recording');
    hideUploadProgress();
    requestWakeLock();
}

function handleStopCommand() {
    if (!isRecording) {
        log('Command', 'Ignoring stop: not recording');
        return;
    }

    isRecording = false;
    stopMediaRecorder();

    wsSend({ type: 'status_update', status: 'status_stopped', data: {} });
    log('Command', 'Recording stopped');

    stopTimer();
    setStatusText('Processing...', 'uploading');
    releaseWakeLock();
    // Finalization happens automatically once all chunks are uploaded (onRecorderStop).
}

function handleDiscardCommand() {
    discardRecording();
}

// ---------------------------------------------------------------------------
// WebSocket connection
// ---------------------------------------------------------------------------

function connectWebSocket() {
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
        return;
    }

    intentionalClose = false;
    log('WS', `Connecting to ${config.wsUrl}`);

    try {
        ws = new WebSocket(config.wsUrl);
    } catch (err) {
        logError('WS', 'Failed to create WebSocket:', err);
        setConnectionUI('disconnected', 'Connection failed');
        scheduleReconnect();
        return;
    }

    ws.addEventListener('open', onWsOpen);
    ws.addEventListener('close', onWsClose);
    ws.addEventListener('error', onWsError);
    ws.addEventListener('message', onWsMessage);
}

function onWsOpen() {
    log('WS', 'Connected');
    reconnectDelay = 1000;
    setConnectionUI('connected', 'Connected');
    startKeepalive();
}

function onWsClose(event) {
    log('WS', `Disconnected (code=${event.code})`);
    stopKeepalive();
    setConnectionUI('disconnected', 'Disconnected');

    if (!intentionalClose) {
        scheduleReconnect();
    }
}

function onWsError(event) {
    logError('WS', 'Error', event);
}

function onWsMessage(event) {
    let data;
    try {
        data = JSON.parse(event.data);
    } catch (_) {
        return;
    }

    const msgType = data.type;

    switch (msgType) {
        case 'ping':
            wsSend({ type: 'pong' });
            break;

        case 'start_recording':
            handleStartCommand();
            break;

        case 'stop_recording':
            handleStopCommand();
            break;

        case 'discard_recording':
            handleDiscardCommand();
            break;

        default:
            log('WS', `Unhandled message type: ${msgType}`, data);
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
        log('WS', `Reconnecting (delay: ${reconnectDelay}ms)...`);
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
// Cleanup
// ---------------------------------------------------------------------------

function cleanup() {
    intentionalClose = true;
    cancelReconnect();
    stopKeepalive();
    stopTimer();
    releaseWakeLock();

    document.removeEventListener('visibilitychange', onVisibilityChange);

    stopMediaRecorder();

    if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
    }

    if (ws) {
        ws.removeEventListener('close', onWsClose);
        ws.close();
        ws = null;
    }
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

window.addEventListener('beforeunload', cleanup);
window.addEventListener('pagehide', cleanup);

async function init() {
    log('Init', 'Starting phone recorder...');

    setConnectionUI(null, 'Connecting...');
    setStatusText('Initializing camera...');

    const cameraReady = await initCamera();
    if (!cameraReady) {
        return;
    }

    connectWebSocket();
}

init();
