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
let currentMimeType = '';   // Actual mimeType used by MediaRecorder
let audioCtx = null;        // Web Audio context for normalization

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

// Discard guard: set true before stopMediaRecorder() so async ondataavailable
// and onstop callbacks don't re-queue chunks or trigger finalization.
let isDiscarding = false;

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
// IndexedDB local chunk backup
// ---------------------------------------------------------------------------
// Every chunk produced by MediaRecorder is mirrored into IndexedDB *as well
// as* uploaded. The local copy is only cleared once the server confirms the
// finalize succeeded and the resulting Video passed its health check.
//
// On phone-recorder load, init() scans IDB for any orphaned recording (a
// recording whose chunks are still present after a refresh / crash / network
// failure) and silently retries the upload. The user is never prompted.

const IDB_DB_NAME    = 'phone-recorder-backup';
const IDB_DB_VERSION = 1;
const IDB_CHUNK_STORE      = 'chunks';     // {id: token+'_'+index, token, index, blob, mime}
const IDB_RECORDING_STORE  = 'recordings'; // {token, started_at, mime_type, finalized}

let _idb = null;
let _idbReady = null;

function _openIdb() {
    if (_idbReady) return _idbReady;
    _idbReady = new Promise((resolve, reject) => {
        if (!('indexedDB' in window)) {
            log('IDB', 'IndexedDB unavailable — local backup disabled');
            resolve(null);
            return;
        }
        const req = window.indexedDB.open(IDB_DB_NAME, IDB_DB_VERSION);
        req.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(IDB_CHUNK_STORE)) {
                const s = db.createObjectStore(IDB_CHUNK_STORE, { keyPath: 'id' });
                s.createIndex('token', 'token', { unique: false });
            }
            if (!db.objectStoreNames.contains(IDB_RECORDING_STORE)) {
                db.createObjectStore(IDB_RECORDING_STORE, { keyPath: 'token' });
            }
        };
        req.onsuccess = (e) => { _idb = e.target.result; resolve(_idb); };
        req.onerror   = (e) => { log('IDB', 'open failed:', e.target.error); resolve(null); };
    });
    return _idbReady;
}

async function idbPutChunk(token, index, blob, mime) {
    const db = await _openIdb();
    if (!db) return;
    return new Promise((resolve) => {
        try {
            const tx = db.transaction([IDB_CHUNK_STORE], 'readwrite');
            tx.objectStore(IDB_CHUNK_STORE).put({
                id:    `${token}_${String(index).padStart(6, '0')}`,
                token, index, blob, mime,
            });
            tx.oncomplete = () => resolve();
            tx.onerror    = () => resolve();
        } catch (e) {
            log('IDB', 'putChunk failed:', e.message);
            resolve();
        }
    });
}

async function idbMarkRecording(token, mime) {
    const db = await _openIdb();
    if (!db) return;
    return new Promise((resolve) => {
        try {
            const tx = db.transaction([IDB_RECORDING_STORE], 'readwrite');
            tx.objectStore(IDB_RECORDING_STORE).put({
                token,
                mime_type:  mime || '',
                started_at: Date.now(),
                finalized:  false,
            });
            tx.oncomplete = () => resolve();
            tx.onerror    = () => resolve();
        } catch (e) {
            resolve();
        }
    });
}

async function idbClearSession(token) {
    const db = await _openIdb();
    if (!db) return;
    return new Promise((resolve) => {
        try {
            const tx = db.transaction([IDB_CHUNK_STORE, IDB_RECORDING_STORE], 'readwrite');
            const chunks = tx.objectStore(IDB_CHUNK_STORE);
            const idx = chunks.index('token');
            const req = idx.openCursor(IDBKeyRange.only(token));
            req.onsuccess = (e) => {
                const cursor = e.target.result;
                if (cursor) { cursor.delete(); cursor.continue(); }
            };
            tx.objectStore(IDB_RECORDING_STORE).delete(token);
            tx.oncomplete = () => resolve();
            tx.onerror    = () => resolve();
        } catch (e) {
            resolve();
        }
    });
}

async function idbListOrphans() {
    const db = await _openIdb();
    if (!db) return [];
    return new Promise((resolve) => {
        try {
            const tx = db.transaction([IDB_RECORDING_STORE], 'readonly');
            const req = tx.objectStore(IDB_RECORDING_STORE).getAll();
            req.onsuccess = () => {
                const all = req.result || [];
                resolve(all.filter(r => !r.finalized));
            };
            req.onerror = () => resolve([]);
        } catch (e) {
            resolve([]);
        }
    });
}

async function idbGetChunks(token) {
    const db = await _openIdb();
    if (!db) return [];
    return new Promise((resolve) => {
        try {
            const tx = db.transaction([IDB_CHUNK_STORE], 'readonly');
            const idx = tx.objectStore(IDB_CHUNK_STORE).index('token');
            const req = idx.getAll(IDBKeyRange.only(token));
            req.onsuccess = () => {
                const all = (req.result || []).slice().sort((a, b) => a.index - b.index);
                resolve(all);
            };
            req.onerror = () => resolve([]);
        } catch (e) {
            resolve([]);
        }
    });
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

    // Explicit audio constraints — passing `true` accepts browser defaults,
    // which on iOS Safari enables AGC + noise suppression + echo cancellation.
    // All three are voice-call DSP and destroy music recording. We disable
    // them and do our own normalization via DynamicsCompressorNode downstream.
    const audioConstraints = config.settings.audio_enabled === true ? {
        autoGainControl:    false,
        echoCancellation:   false,
        noiseSuppression:   false,
        // Some Chromium-derived browsers still honor these legacy names:
        googAutoGainControl: false,
        googEchoCancellation: false,
        googNoiseSuppression: false,
        // High-quality capture for music:
        sampleRate:   { ideal: 48000 },
        channelCount: { ideal: 2 },
    } : false;

    const constraints = {
        video: {
            width:      { ideal: resolution.width },
            height:     { ideal: resolution.height },
            frameRate:  { ideal: frameRate },
            facingMode: { ideal: 'environment' },
        },
        audio: audioConstraints,
    };

    try {
        mediaStream = await navigator.mediaDevices.getUserMedia(constraints);

        // Verify we actually got a video track. iOS Safari can return audio-only
        // streams in some failure modes.
        const videoTracks = mediaStream.getVideoTracks();
        const audioTracks = mediaStream.getAudioTracks();
        log('Camera', `tracks: video=${videoTracks.length}, audio=${audioTracks.length}`);

        if (videoTracks.length === 0) {
            setStatusText('No video track — check camera permission');
            setConnectionUI('disconnected', 'No video');
            wsSend({ type: 'status_update', status: 'error', data: { message: 'Camera granted but no video track returned' } });
            return false;
        }

        // Apply audio dynamics compression for consistent volume during music playback.
        if (config.settings.audio_enabled && audioTracks.length > 0) {
            mediaStream = applyAudioNormalization(mediaStream);
        }

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
// Audio normalization (Web Audio API DynamicsCompressor)
// ---------------------------------------------------------------------------
// Routes audio through a dynamics compressor so loud music gets quieter and
// quiet sounds get louder. Keeps the perceived volume roughly constant.
function applyAudioNormalization(rawStream) {
    const AudioCtxCls = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtxCls) {
        log('Audio', 'Web Audio API unavailable — recording raw audio');
        return rawStream;
    }
    try {
        audioCtx = new AudioCtxCls();
        const source = audioCtx.createMediaStreamSource(rawStream);

        // Light "glue" compression — assumes browser AGC has been disabled
        // upstream. Goal is to catch occasional peaks, not to flatten the
        // dynamics of the music.
        //   threshold -18 dB    : only engage on louder material
        //   knee 12 dB          : moderate soft knee, transparent
        //   ratio 3:1           : gentle, doesn't pump on transients
        //   attack 20 ms        : slow enough that drum hits pass through
        //   release 200 ms      : musical, not audible as gain riding
        const compressor = audioCtx.createDynamicsCompressor();
        compressor.threshold.value = -18;
        compressor.knee.value = 12;
        compressor.ratio.value = 3;
        compressor.attack.value = 0.020;
        compressor.release.value = 0.200;

        // Small make-up gain to compensate for compression headroom.
        const makeup = audioCtx.createGain();
        makeup.gain.value = 1.2;

        const dest = audioCtx.createMediaStreamDestination();
        source.connect(compressor);
        compressor.connect(makeup);
        makeup.connect(dest);

        const processedStream = new MediaStream([
            ...rawStream.getVideoTracks(),
            ...dest.stream.getAudioTracks(),
        ]);
        log('Audio', 'Dynamics compressor active');
        return processedStream;
    } catch (err) {
        log('Audio', 'Normalization failed, using raw stream:', err.message);
        return rawStream;
    }
}

// ---------------------------------------------------------------------------
// MediaRecorder MIME type selection
// ---------------------------------------------------------------------------

// Detect Safari/iOS where MP4/H.264 is the only reliable container for video recording.
// WebM via MediaRecorder on iOS Safari is unreliable and frequently produces
// audio-only files even when isTypeSupported() returns true.
function isSafariOrIOS() {
    const ua = navigator.userAgent;
    if (/iPad|iPhone|iPod/.test(ua)) return true;
    return /Safari/.test(ua) && !/Chrome|Chromium|CriOS|FxiOS|EdgiOS/.test(ua);
}

function selectMimeType() {
    const vc = config.settings.video_codec || 'vp8';
    const ac = config.settings.audio_codec || 'opus';
    const hasAudio = config.settings.audio_enabled === true;

    // Prioritized list of MIME types — order matters per-platform.
    const candidates = [];

    if (isSafariOrIOS()) {
        // Safari/iOS: MP4/H.264 first. iOS sometimes lies about WebM support and
        // produces audio-only files for unsupported video codecs.
        candidates.push('video/mp4;codecs="avc1.42E01E,mp4a.40.2"'); // H.264 baseline + AAC
        candidates.push('video/mp4;codecs=h264,aac');
        candidates.push('video/mp4;codecs=avc1');
        candidates.push('video/mp4;codecs=h264');
        candidates.push('video/mp4');
    }

    if (hasAudio) {
        candidates.push(`video/webm;codecs=${vc},${ac}`);
    }
    candidates.push(`video/webm;codecs=${vc}`);
    candidates.push('video/webm;codecs=vp9,opus');
    candidates.push('video/webm;codecs=vp8,opus');
    candidates.push('video/webm;codecs=vp9');
    candidates.push('video/webm;codecs=vp8');
    candidates.push('video/webm');

    // MP4 fallback for non-Safari browsers
    if (!isSafariOrIOS()) {
        candidates.push('video/mp4;codecs=h264,aac');
        candidates.push('video/mp4');
    }

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

        // Remember the actual mimeType the browser settled on so the server can
        // save the file with the correct extension (mp4 vs webm).
        currentMimeType = recorder.mimeType || mimeType || '';
        log('Recorder', `Created with mimeType=${currentMimeType}, videoBps=${videoBps}`);
        return recorder;
    } catch (err) {
        logError('Recorder', 'Failed to create MediaRecorder:', err);
        wsSend({ type: 'status_update', status: 'error', data: { message: `MediaRecorder creation failed: ${err.message}` } });
        return null;
    }
}

function onDataAvailable(event) {
    // If we're discarding, throw away any buffered data the MediaRecorder emits.
    if (isDiscarding) return;
    if (event.data && event.data.size > 0) {
        totalBytesQueued += event.data.size;
        const myIndex = chunkIndex + chunkQueue.length;  // index the upload will use
        chunkQueue.push(event.data);
        // Mirror to IndexedDB asynchronously — never blocks upload.
        idbPutChunk(config.token, myIndex, event.data, currentMimeType).catch(() => {});
        processChunkQueue();
    }
}

function onRecorderStop() {
    log('Recorder', 'Stopped');
    // If we're discarding, don't trigger finalization — the discard handler
    // already cleared the queue and sent the discard request.
    if (isDiscarding) {
        isDiscarding = false;
        return;
    }
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
            body: JSON.stringify({ mime_type: currentMimeType }),
        });

        if (!response.ok) {
            throw new Error(`Finalize failed: ${response.status}`);
        }

        const data = await response.json();
        const videoId = data.video_id;
        const health = data.health_status || 'unknown';
        const healthDetail = data.health_detail || '';

        wsSend({
            type: 'status_update',
            status: 'status_upload_complete',
            data: { video_id: videoId, health_status: health, health_detail: healthDetail },
        });
        log('Finalize', `Complete. video_id=${videoId}  health=${health}`);

        // Only clear the local backup if the server confirmed the recording
        // is healthy. If it's flagged as audio-only / corrupted / empty, keep
        // the IDB copy around so a future recovery pass can re-upload it.
        if (health === 'ok' || health === 'unknown') {
            idbClearSession(config.token).catch(() => {});
        } else {
            log('Finalize', `Recording flagged unhealthy (${health}). Keeping local backup.`);
        }

        if (health === 'ok' || health === 'unknown') {
            setStatusText('Upload complete');
        } else {
            setStatusText(`Saved but flagged: ${health.replace('_', ' ')}`);
        }
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

    // Set the guard BEFORE stopping the recorder.
    // MediaRecorder.stop() is async — ondataavailable fires after this call
    // returns, so we must flag isDiscarding first to suppress those callbacks.
    isDiscarding = true;

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

    // User explicitly discarded — wipe the local backup too.
    idbClearSession(config.token).catch(() => {});

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
    isDiscarding = false;
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

    // Mark the recording in IDB so we can recover if finalize never completes.
    idbMarkRecording(config.token, currentMimeType).catch(() => {});

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

// ---------------------------------------------------------------------------
// Silent recovery of orphan recordings
// ---------------------------------------------------------------------------
// On page load, look for any recording in IndexedDB that was never marked
// finalized (its chunks survived a crash, refresh, or network failure).
// Try to re-upload its chunks to the server's existing endpoint. The server's
// chunk endpoint is idempotent (writes by chunk_index), so even if the
// chunks already partially landed, the file ends up correct. If the session
// token is still valid, finalize. If finalize fails (e.g. session expired),
// keep the IDB copy for a future recovery attempt.

async function recoverOrphanRecordings() {
    let orphans = [];
    try {
        orphans = await idbListOrphans();
    } catch (e) {
        return;
    }
    if (!orphans.length) return;

    log('Recovery', `Found ${orphans.length} orphan recording(s) in local backup`);

    for (const rec of orphans) {
        // Skip the current session — that's an active recording, not an orphan.
        if (rec.token === config.token) continue;

        const chunks = await idbGetChunks(rec.token);
        if (!chunks.length) {
            // Empty record, just remove it.
            await idbClearSession(rec.token);
            continue;
        }

        log('Recovery', `Re-uploading ${chunks.length} chunk(s) for token=${rec.token}`);
        let uploadedAll = true;
        for (const c of chunks) {
            try {
                const fd = new FormData();
                fd.append('chunk', c.blob, `chunk_${c.index}`);
                fd.append('chunk_index', String(c.index));
                // The chunk upload URL is the same path with the orphan token.
                const url = config.chunkUrl.replace(config.token, rec.token);
                const resp = await fetch(url, { method: 'POST', body: fd });
                if (!resp.ok) { uploadedAll = false; break; }
            } catch (e) {
                uploadedAll = false;
                break;
            }
        }
        if (!uploadedAll) {
            log('Recovery', `Upload failed for token=${rec.token} — keeping backup`);
            continue;
        }

        // Try to finalize.
        try {
            const url = config.finalizeUrl.replace(config.token, rec.token);
            const resp = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mime_type: rec.mime_type || '' }),
            });
            if (resp.ok) {
                const data = await resp.json();
                const ok = data.health_status === 'ok' || data.health_status === 'unknown';
                if (ok) {
                    await idbClearSession(rec.token);
                    log('Recovery', `Recovered token=${rec.token} → video_id=${data.video_id}`);
                } else {
                    log('Recovery', `Recovered but flagged ${data.health_status}; keeping backup`);
                }
            } else if (resp.status === 403) {
                // Session expired — there's nothing we can do server-side. Keep
                // the backup so the user can re-record fresh; the chunks will
                // eventually age out via the manual cleanup below.
                log('Recovery', `Session ${rec.token} expired (403). Cannot finalize.`);
            } else {
                log('Recovery', `Finalize returned ${resp.status}; keeping backup`);
            }
        } catch (e) {
            log('Recovery', `Finalize error for token=${rec.token}:`, e.message);
        }
    }

    // Drop very old orphans (>24h) regardless — they're never going to
    // recover and they're just taking up space.
    try {
        const stale = (await idbListOrphans())
            .filter(r => Date.now() - (r.started_at || 0) > 24 * 3600 * 1000);
        for (const r of stale) {
            await idbClearSession(r.token);
            log('Recovery', `Pruned stale orphan from ${new Date(r.started_at).toISOString()}`);
        }
    } catch (e) {}
}

async function init() {
    log('Init', 'Starting phone recorder...');

    setConnectionUI(null, 'Connecting...');
    setStatusText('Initializing camera...');

    const cameraReady = await initCamera();
    if (!cameraReady) {
        return;
    }

    connectWebSocket();

    // Run orphan recovery in the background — never block the main flow.
    recoverOrphanRecordings().catch((e) => log('Recovery', 'failed:', e.message));
}

init();
