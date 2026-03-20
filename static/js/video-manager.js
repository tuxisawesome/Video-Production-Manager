'use strict';

// ---------------------------------------------------------------------------
// CSRF helper
// ---------------------------------------------------------------------------

function getCSRFToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------

const sidebar        = document.getElementById('video-sidebar');
const sidebarOverlay = document.getElementById('sidebar-overlay');
const sidebarPlayer  = document.getElementById('sidebar-player');
const sidebarTitle   = document.getElementById('sidebar-title');
const sidebarFields  = {
    filename: document.getElementById('sidebar-filename'),
    duration: document.getElementById('sidebar-duration'),
    size:     document.getElementById('sidebar-size'),
    elo:      document.getElementById('sidebar-elo'),
};
const sidebarDownloadLink = document.getElementById('sidebar-download');
const sidebarDeleteBtn    = document.getElementById('sidebar-delete-btn');

const uploadForm  = document.getElementById('video-upload-form');
const uploadInput = document.getElementById('video-upload-input');

const deleteVideoDialog = document.getElementById('delete-video-dialog');
const deleteVideoForm   = document.getElementById('delete-video-form');
const deleteVideoName   = document.getElementById('delete-video-name');

const deleteProjectDialog = document.getElementById('delete-project-dialog');

// ---------------------------------------------------------------------------
// Utility: format bytes
// ---------------------------------------------------------------------------

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

// ---------------------------------------------------------------------------
// Sidebar: open / close
// ---------------------------------------------------------------------------

function openSidebar(cardEl) {
    const videoUrl      = cardEl.dataset.videoUrl;
    const videoName     = cardEl.dataset.videoName;
    const videoDuration = cardEl.dataset.videoDuration;
    const videoSize     = cardEl.dataset.videoSize;
    const videoElo      = cardEl.dataset.videoElo;
    const videoId       = cardEl.dataset.videoId;

    // Set video player source
    sidebarPlayer.src = videoUrl;

    // Populate metadata
    sidebarTitle.textContent            = videoName;
    sidebarFields.filename.textContent  = videoName;
    sidebarFields.duration.textContent  = videoDuration !== '--'
        ? parseFloat(videoDuration).toFixed(1) + 's'
        : 'Unknown';
    sidebarFields.size.textContent      = formatBytes(parseInt(videoSize, 10));
    sidebarFields.elo.textContent       = parseFloat(videoElo).toFixed(0);

    // Build download URL relative to current page path
    // Current page is /projects/<id>/, download is /projects/<id>/videos/<vid>/download/
    const basePath = window.location.pathname.replace(/\/$/, '');
    sidebarDownloadLink.href = basePath + '/videos/' + videoId + '/download/';

    // Wire up sidebar delete button
    sidebarDeleteBtn.onclick = function () {
        confirmDeleteVideo(videoId, videoName);
    };

    // Show sidebar and overlay
    sidebar.classList.add('open');
    sidebarOverlay.classList.add('open');
}

function closeSidebar() {
    sidebarPlayer.pause();
    sidebarPlayer.removeAttribute('src');
    sidebarPlayer.load(); // reset player
    sidebar.classList.remove('open');
    sidebarOverlay.classList.remove('open');
}

// ---------------------------------------------------------------------------
// Delete video confirmation
// ---------------------------------------------------------------------------

function confirmDeleteVideo(videoId, videoName) {
    deleteVideoName.textContent = videoName;
    const basePath = window.location.pathname.replace(/\/$/, '');
    deleteVideoForm.action = basePath + '/videos/' + videoId + '/delete/';
    deleteVideoDialog.classList.add('open');
}

// ---------------------------------------------------------------------------
// Upload trigger
// ---------------------------------------------------------------------------

if (uploadInput && uploadForm) {
    uploadInput.addEventListener('change', function () {
        if (uploadInput.files.length > 0) {
            uploadForm.submit();
        }
    });
}

// ---------------------------------------------------------------------------
// Keyboard: close sidebar with Escape
// ---------------------------------------------------------------------------

document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
        closeSidebar();
        // Also close any open dialogs
        if (deleteVideoDialog) deleteVideoDialog.classList.remove('open');
        if (deleteProjectDialog) deleteProjectDialog.classList.remove('open');
    }
});

// ---------------------------------------------------------------------------
// Close overlay click for dialogs
// ---------------------------------------------------------------------------

if (sidebarOverlay) {
    sidebarOverlay.addEventListener('click', closeSidebar);
}

if (deleteVideoDialog) {
    deleteVideoDialog.addEventListener('click', function (e) {
        if (e.target === deleteVideoDialog) {
            deleteVideoDialog.classList.remove('open');
        }
    });
}

if (deleteProjectDialog) {
    deleteProjectDialog.addEventListener('click', function (e) {
        if (e.target === deleteProjectDialog) {
            deleteProjectDialog.classList.remove('open');
        }
    });
}

// ---------------------------------------------------------------------------
// Expose functions globally for inline onclick handlers in the template
// ---------------------------------------------------------------------------

window.openSidebar        = openSidebar;
window.closeSidebar       = closeSidebar;
window.confirmDeleteVideo = confirmDeleteVideo;
window.formatBytes        = formatBytes;
