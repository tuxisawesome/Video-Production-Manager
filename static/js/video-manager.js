'use strict';

// ---------------------------------------------------------------------------
// Page data & CSRF
// ---------------------------------------------------------------------------

const _pageData = JSON.parse(document.getElementById('page-data')?.textContent || '{}');
const _isOwner  = _pageData.isOwner !== false;
const _csrfToken = _pageData.csrfToken || '';

// Placeholder used in the delete URL template (token slot).
const _PLACEHOLDER = '00000000-0000-0000-0000-000000000000';
const _shareLinkDeleteBase = _pageData.shareLinkDeleteBaseUrl || '';

function getCSRFToken() {
    return _csrfToken ||
        document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
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

const commentsList  = document.getElementById('comments-list');
const commentText   = document.getElementById('comment-text');
const commentTs     = document.getElementById('comment-timestamp');

// Current sidebar state
let _currentCard = null;
let _commentListUrl    = '';
let _commentCreateUrl  = '';
let _currentProjectPk  = '';
let _currentGalleryPk  = '';
let _currentVideoId    = '';
let _videoShareListUrl = '';    // per-video, set from card data attribute
let _videoShareCreateUrl = '';  // per-video, set from card data attribute

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

function formatTimestamp(secs) {
    if (secs == null) return '';
    const m = Math.floor(secs / 60);
    const s = (secs % 60).toFixed(1).padStart(4, '0');
    return m > 0 ? `${m}:${s}` : `${s}s`;
}

// ---------------------------------------------------------------------------
// Sidebar: open / close
// ---------------------------------------------------------------------------

function openSidebar(cardEl) {
    _currentCard      = cardEl;
    const videoUrl        = cardEl.dataset.videoUrl;
    const videoName       = cardEl.dataset.videoName;
    const videoDuration   = cardEl.dataset.videoDuration;
    const videoSize       = cardEl.dataset.videoSize;
    const videoElo        = cardEl.dataset.videoElo;
    const videoId         = cardEl.dataset.videoId;
    _commentListUrl      = cardEl.dataset.commentUrl || '';
    _commentCreateUrl    = cardEl.dataset.commentCreateUrl || '';
    _currentProjectPk    = cardEl.dataset.projectPk || '';
    _currentGalleryPk    = cardEl.dataset.galleryPk || '';
    _currentVideoId      = videoId;
    _videoShareListUrl   = cardEl.dataset.shareListUrl || '';
    _videoShareCreateUrl = cardEl.dataset.shareCreateUrl || '';

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

    // Build download URL
    const basePath = window.location.pathname.replace(/\/$/, '');
    sidebarDownloadLink.href = basePath + '/videos/' + videoId + '/download/';

    // Wire up sidebar delete button (owners only)
    if (sidebarDeleteBtn) {
        sidebarDeleteBtn.onclick = function () {
            confirmDeleteVideo(videoId, videoName);
        };
    }

    // Clear comment input
    if (commentText) commentText.value = '';
    if (commentTs)   commentTs.value   = '';

    // Load comments
    if (_commentListUrl) loadComments();

    // Load video share links (owner only, URL provided by card data attribute)
    if (_isOwner && _videoShareListUrl) loadVideoShareLinks();

    // Show sidebar
    sidebar.classList.add('open');
    sidebarOverlay.classList.add('open');
}

function closeSidebar() {
    sidebarPlayer.pause();
    sidebarPlayer.removeAttribute('src');
    sidebarPlayer.load();
    sidebar.classList.remove('open');
    sidebarOverlay.classList.remove('open');
    _currentCard = null;
}

// ---------------------------------------------------------------------------
// Comments
// ---------------------------------------------------------------------------

async function loadComments() {
    if (!_commentListUrl) return;
    try {
        const resp = await fetch(_commentListUrl, { headers: { 'Accept': 'application/json' } });
        if (!resp.ok) return;
        const { comments } = await resp.json();
        renderComments(comments);
    } catch (e) {
        console.error('[Comments] load error', e);
    }
}

function renderComments(comments) {
    if (!commentsList) return;
    if (!comments.length) {
        commentsList.innerHTML = '<span class="md-body-small text-on-surface-variant">No comments yet.</span>';
        return;
    }
    commentsList.innerHTML = comments.map(c => {
        const ts = c.timestamp_seconds != null
            ? `<button class="md-button-text" style="font-size:12px; padding:0 4px; min-width:0;"
                       onclick="seekToTimestamp(${c.timestamp_seconds})">@${formatTimestamp(c.timestamp_seconds)}</button>`
            : '';
        const del = c.is_own || _isOwner
            ? `<button class="md-icon-button" style="margin-left:auto; color:var(--md-sys-color-error); flex-shrink:0;"
                       onclick="deleteComment(${c.id})" title="Delete">
                 <span class="material-symbols-outlined" style="font-size:16px;">delete</span>
               </button>`
            : '';
        return `
            <div style="background:var(--md-sys-color-surface-container); border-radius:var(--md-sys-shape-corner-small);
                        padding:10px 12px; display:flex; flex-direction:column; gap:4px;" data-comment-id="${c.id}">
              <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
                <span class="md-label-medium">${escapeHtml(c.author)}</span>
                ${ts}
                <span class="md-body-small text-on-surface-variant" style="margin-left:auto; white-space:nowrap;">${formatDate(c.created_at)}</span>
                ${del}
              </div>
              <p class="md-body-medium" style="margin:0; white-space:pre-wrap;">${escapeHtml(c.text)}</p>
            </div>`;
    }).join('');
}

async function submitComment() {
    if (!_commentCreateUrl) return;
    const text = commentText ? commentText.value.trim() : '';
    if (!text) return;
    const ts = commentTs && commentTs.value !== '' ? parseFloat(commentTs.value) : null;

    try {
        const resp = await fetch(_commentCreateUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({ text, timestamp_seconds: ts }),
        });
        if (!resp.ok) {
            const err = await resp.json();
            alert(err.error || 'Failed to post comment.');
            return;
        }
        if (commentText) commentText.value = '';
        if (commentTs)   commentTs.value   = '';
        loadComments();
    } catch (e) {
        console.error('[Comments] submit error', e);
    }
}

async function deleteComment(commentId) {
    const deleteUrl = `/projects/${_currentProjectPk}/galleries/${_currentGalleryPk}/videos/${_currentVideoId}/comments/${commentId}/delete/`;
    try {
        const resp = await fetch(deleteUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (resp.ok) loadComments();
    } catch (e) {
        console.error('[Comments] delete error', e);
    }
}

function seekToTimestamp(secs) {
    if (sidebarPlayer) sidebarPlayer.currentTime = secs;
}

function useVideoTimestamp() {
    if (sidebarPlayer && commentTs) {
        commentTs.value = sidebarPlayer.currentTime.toFixed(1);
    }
}

// ---------------------------------------------------------------------------
// Video share links (owner sidebar)
// ---------------------------------------------------------------------------

function _shareErrHtml(msg) {
    return `<span class="md-body-small" style="color:var(--md-sys-color-error);">${escapeHtml(msg)}</span>`;
}

async function loadVideoShareLinks() {
    const container = document.getElementById('video-share-links');
    if (!container) return;

    // URL is baked into the card's data attribute — if missing, the section
    // shouldn't be visible anyway (owner-only template guard).
    if (!_videoShareListUrl) {
        container.innerHTML = _shareErrHtml('No URL — refresh and try again.');
        return;
    }

    container.innerHTML = '<span class="md-body-small text-on-surface-variant">Loading…</span>';
    try {
        const resp = await fetch(_videoShareListUrl, {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' },
        });
        if (!resp.ok) {
            container.innerHTML = _shareErrHtml(`Server returned ${resp.status}. Check server logs.`);
            console.error('[Share] list failed:', resp.status, _videoShareListUrl);
            return;
        }
        let data;
        try {
            data = await resp.json();
        } catch {
            container.innerHTML = _shareErrHtml('Unexpected response (not JSON). Check server logs.');
            return;
        }
        renderVideoShareLinks(data.links || []);
    } catch (e) {
        container.innerHTML = _shareErrHtml('Network error. Check console.');
        console.error('[Share] load error', e);
    }
}

function renderVideoShareLinks(links) {
    const container = document.getElementById('video-share-links');
    if (!container) return;
    if (!links.length) {
        container.innerHTML = '<span class="md-body-small text-on-surface-variant">No share links yet.</span>';
        return;
    }
    container.innerHTML = links.map(sl => `
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
          <span class="md-chip" style="background:var(--md-sys-color-secondary-container); color:var(--md-sys-color-on-secondary-container); font-size:12px; white-space:nowrap;">
            ${escapeHtml(sl.access_type_display)}${sl.has_password ? ' 🔒' : ''}
          </span>
          <input type="text" readonly value="${escapeHtml(sl.url)}"
                 style="flex:1; min-width:0; font-size:11px; font-family:monospace; padding:4px 8px;
                        border:1px solid var(--md-sys-color-outline-variant); border-radius:4px;
                        background:var(--md-sys-color-surface-container); color:var(--md-sys-color-on-surface);
                        cursor:pointer;"
                 onclick="this.select(); document.execCommand('copy');" title="Click to copy">
          <button class="md-icon-button" style="color:var(--md-sys-color-error); flex-shrink:0;"
                  onclick="deleteVideoShareLink('${escapeHtml(sl.delete_url)}')" title="Delete">
            <span class="material-symbols-outlined" style="font-size:18px;">delete</span>
          </button>
        </div>`).join('');
}

async function createVideoShareLink() {
    if (!_videoShareCreateUrl) {
        alert('Cannot create link: no URL available. Try refreshing the page.');
        return;
    }
    const btn = document.querySelector('#video-sidebar button[onclick="createVideoShareLink();"]');
    if (btn) btn.disabled = true;

    const accessType = document.querySelector('input[name="vsl_access"]:checked')?.value || 'view';
    const password   = (document.getElementById('vsl-password')?.value || '').trim();

    try {
        const resp = await fetch(_videoShareCreateUrl, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify({ access_type: accessType, password }),
        });

        if (!resp.ok) {
            let msg = `Server error ${resp.status}.`;
            try { const e = await resp.json(); msg = e.error || msg; } catch {}
            alert(msg);
            return;
        }

        let data;
        try { data = await resp.json(); } catch {
            alert('Unexpected response from server. Check server logs.');
            return;
        }

        const pw = document.getElementById('vsl-password');
        if (pw) pw.value = '';
        loadVideoShareLinks();
    } catch (e) {
        alert('Network error creating share link. Check console.');
        console.error('[Share] create error', e);
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function deleteVideoShareLink(deleteUrl) {
    // deleteUrl is the full relative URL returned by the list endpoint.
    if (!deleteUrl) return;
    try {
        const resp = await fetch(deleteUrl, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'X-CSRFToken': getCSRFToken(),
                'X-Requested-With': 'XMLHttpRequest',
            },
        });
        if (resp.ok) {
            loadVideoShareLinks();
        } else {
            alert(`Failed to delete link (${resp.status}).`);
        }
    } catch (e) {
        alert('Network error deleting link.');
        console.error('[Share] delete error', e);
    }
}

// ---------------------------------------------------------------------------
// Delete video confirmation
// ---------------------------------------------------------------------------

function confirmDeleteVideo(videoId, videoName) {
    if (!deleteVideoDialog) return;
    deleteVideoName.textContent = videoName;
    const basePath = window.location.pathname.replace(/\/$/, '');
    deleteVideoForm.action = basePath + '/videos/' + videoId + '/delete/';
    deleteVideoDialog.classList.add('open');
}

// ---------------------------------------------------------------------------
// Copy share link helper
// ---------------------------------------------------------------------------

function showCopied(inputEl) {
    inputEl.select();
    try { document.execCommand('copy'); } catch (_) {}
    const orig = inputEl.style.borderColor;
    inputEl.style.borderColor = 'var(--md-sys-color-primary)';
    setTimeout(() => { inputEl.style.borderColor = orig; }, 1200);
}

// ---------------------------------------------------------------------------
// HTML helpers
// ---------------------------------------------------------------------------

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function formatDate(isoStr) {
    const d = new Date(isoStr);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

// ---------------------------------------------------------------------------
// Upload trigger
// ---------------------------------------------------------------------------

if (uploadInput && uploadForm) {
    uploadInput.addEventListener('change', function () {
        if (uploadInput.files.length > 0) uploadForm.submit();
    });
}

// ---------------------------------------------------------------------------
// Keyboard: Escape closes sidebar / dialogs
// ---------------------------------------------------------------------------

document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
        closeSidebar();
        if (deleteVideoDialog)   deleteVideoDialog.classList.remove('open');
        if (deleteProjectDialog) deleteProjectDialog.classList.remove('open');
        const shareDialog = document.getElementById('share-dialog');
        if (shareDialog)         shareDialog.classList.remove('open');
    }
});

// ---------------------------------------------------------------------------
// Dialog click-outside to close
// ---------------------------------------------------------------------------

if (deleteVideoDialog) {
    deleteVideoDialog.addEventListener('click', e => {
        if (e.target === deleteVideoDialog) deleteVideoDialog.classList.remove('open');
    });
}
if (deleteProjectDialog) {
    deleteProjectDialog.addEventListener('click', e => {
        if (e.target === deleteProjectDialog) deleteProjectDialog.classList.remove('open');
    });
}

// ---------------------------------------------------------------------------
// Expose globals
// ---------------------------------------------------------------------------

window.openSidebar           = openSidebar;
window.closeSidebar          = closeSidebar;
window.confirmDeleteVideo    = confirmDeleteVideo;
window.formatBytes           = formatBytes;
window.submitComment         = submitComment;
window.deleteComment         = deleteComment;
window.seekToTimestamp       = seekToTimestamp;
window.useVideoTimestamp     = useVideoTimestamp;
window.showCopied            = showCopied;
window.createVideoShareLink  = createVideoShareLink;
window.deleteVideoShareLink  = deleteVideoShareLink;
