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
const _renameVideoBase     = _pageData.renameVideoBaseUrl   || '';
const _moveVideoBase       = _pageData.moveVideoBaseUrl     || '';
const _bulkMoveUrl         = _pageData.bulkMoveUrl          || '';
const _bulkDeleteUrl       = _pageData.bulkDeleteUrl        || '';
const _galleryPickerUrl    = _pageData.galleryPickerUrl     || '';

// Cached gallery list for the move dialog (loaded once per page).
let _galleryPickerCache = null;
// Multi-select state
let _selectMode = false;
const _selectedIds = new Set();

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
let _commentListUrl      = '';
let _commentCreateUrl    = '';
let _currentProjectPk    = '';
let _currentGalleryPk    = '';
let _currentVideoId      = '';
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

// m:ss / h:mm:ss formatting for video durations (matches the duration_mmss
// template filter so card labels and the sidebar agree).
function formatDuration(totalSeconds) {
    const t = Number(totalSeconds);
    if (!Number.isFinite(t) || t <= 0) return '--';
    const rounded = Math.round(t);
    const h = Math.floor(rounded / 3600);
    const m = Math.floor((rounded % 3600) / 60);
    const s = rounded % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    return `${m}:${String(s).padStart(2, '0')}`;
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
    _videoShareCreateUrl = cardEl.dataset.shareCreateUrl || '';

    // Set video player source
    sidebarPlayer.src = videoUrl;

    // Populate metadata
    sidebarTitle.textContent            = videoName;
    sidebarFields.filename.textContent  = videoName;
    sidebarFields.duration.textContent  = videoDuration !== '--'
        ? formatDuration(parseFloat(videoDuration))
        : 'Unknown';
    sidebarFields.size.textContent      = formatBytes(parseInt(videoSize, 10));
    sidebarFields.elo.textContent       = parseFloat(videoElo).toFixed(0);

    // Health banner — show only if the server flagged this recording.
    const healthBanner = document.getElementById('sidebar-health-banner');
    if (healthBanner) {
        const health = cardEl.dataset.videoHealth || 'unknown';
        const detail = cardEl.dataset.videoHealthDetail || '';
        if (health && health !== 'unknown' && health !== 'ok') {
            const titleEl = document.getElementById('sidebar-health-title');
            const detailEl = document.getElementById('sidebar-health-detail');
            const labels = {
                'audio_only': 'Audio only — no video track',
                'corrupted':  'Corrupted — container unreadable',
                'empty':      'Empty — no decodable streams',
            };
            if (titleEl) titleEl.textContent = labels[health] || 'Recording problem detected';
            if (detailEl) detailEl.textContent = detail || 'Try re-recording this clip.';
            healthBanner.style.display = 'flex';
        } else {
            healthBanner.style.display = 'none';
        }
    }

    // Build download URL
    const basePath = window.location.pathname.replace(/\/$/, '');
    sidebarDownloadLink.href = basePath + '/videos/' + videoId + '/download/';

    // Wire up sidebar delete button (owners only)
    if (sidebarDeleteBtn) {
        sidebarDeleteBtn.onclick = function () {
            confirmDeleteVideo(videoId, videoName);
        };
    }

    // Wire up Generate share link button (owners only)
    const genBtn = document.getElementById('vsl-generate-btn');
    if (genBtn) genBtn.onclick = createVideoShareLink;

    // Clear comment input
    if (commentText) commentText.value = '';
    if (commentTs)   commentTs.value   = '';

    // Load comments
    if (_commentListUrl) loadComments();

    // Render video share links synchronously from embedded JSON in the card
    if (_isOwner) {
        const slDataEl = cardEl.querySelector('.vsl-data');
        const vslContainer = document.getElementById('video-share-links');
        if (vslContainer) {
            if (slDataEl) {
                try {
                    renderVideoShareLinks(JSON.parse(slDataEl.textContent));
                } catch (e) {
                    renderVideoShareLinks([]);
                    console.error('[Share] JSON parse error', e);
                }
            } else {
                renderVideoShareLinks([]);
            }
        }
    }

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

// Server's get_access_type_display() can return long phrases like
// "Commentator + Download" that push the URL field off the narrow sidebar.
// Collapse to a single word chip.
function _shortRole(displayText) {
    const t = (displayText || '').toLowerCase();
    if (t.includes('comment')) return 'Commentator';
    if (t.includes('rank'))    return 'Rank';
    return 'View';
}

function _shareRowHtml(sl) {
    const role = _shortRole(sl.access_type_display);
    const lock = sl.has_password
        ? '<span class="material-symbols-outlined" style="font-size:14px; vertical-align:-2px; margin-left:2px;">lock</span>'
        : '';
    return `
      <span class="md-chip" style="background:var(--md-sys-color-secondary-container); color:var(--md-sys-color-on-secondary-container); font-size:12px; padding:2px 8px; border-radius:8px; white-space:nowrap; flex-shrink:0;">
        ${escapeHtml(role)}${lock}
      </span>
      <input type="text" readonly value="${escapeHtml(sl.url)}"
             style="flex:1; min-width:0; font-size:11px; font-family:monospace; padding:4px 8px;
                    border:1px solid var(--md-sys-color-outline-variant); border-radius:4px;
                    background:var(--md-sys-color-surface-container); color:var(--md-sys-color-on-surface);
                    cursor:pointer;"
             onclick="this.select(); document.execCommand('copy');" title="Click to copy">
      <button class="md-icon-button" style="color:var(--md-sys-color-error); flex-shrink:0;"
              onclick="deleteVideoShareLink('${escapeHtml(sl.token)}')" title="Delete">
        <span class="material-symbols-outlined" style="font-size:18px;">delete</span>
      </button>`;
}

function renderVideoShareLinks(links) {
    const container = document.getElementById('video-share-links');
    if (!container) return;
    if (!links.length) {
        container.innerHTML = '<span class="md-body-small text-on-surface-variant vsl-empty">No share links yet.</span>';
        return;
    }
    container.innerHTML = links.map(sl => `
        <div data-token="${escapeHtml(sl.token)}" style="display:flex; align-items:center; gap:8px; flex-wrap:nowrap;">
          ${_shareRowHtml(sl)}
        </div>`).join('');
}

function _appendVideoShareLink(sl) {
    const container = document.getElementById('video-share-links');
    if (!container) return;
    const empty = container.querySelector('.vsl-empty');
    if (empty) empty.remove();
    const div = document.createElement('div');
    div.setAttribute('data-token', sl.token);
    div.style.cssText = 'display:flex; align-items:center; gap:8px; flex-wrap:nowrap;';
    div.innerHTML = _shareRowHtml(sl);
    container.appendChild(div);
    // Persist the new link into the card's embedded JSON so it survives
    // closing/reopening the sidebar.
    _updateCardShareLinks(arr => [...arr, {
        token: sl.token,
        access_type_display: sl.access_type_display,
        has_password: !!sl.has_password,
        url: sl.url,
        delete_url: sl.delete_url || '',
    }]);
    // Mirror the new link into the gallery-level "Video Share Links" table.
    const videoName = _currentCard ? (_currentCard.dataset.videoName || '') : '';
    _appendGalleryShareRow(sl, videoName);
}

// Build a row matching the server-rendered #gallery-share-tbody markup and
// keep the summary count + empty-state in sync.
function _appendGalleryShareRow(sl, videoName) {
    const tbody = document.getElementById('gallery-share-tbody');
    if (!tbody) return;
    const role = _shortRole(sl.access_type_display);
    const lockHtml = sl.has_password
        ? '<span class="material-symbols-outlined" style="font-size:14px; vertical-align:-2px; margin-left:2px;">lock</span>'
        : '';

    const row = document.createElement('div');
    row.setAttribute('data-token', sl.token);
    row.style.cssText = 'display:flex; align-items:center; gap:8px; flex-wrap:nowrap; ' +
                        'padding:8px 4px; border-bottom:1px solid var(--md-sys-color-outline-variant);';
    row.innerHTML = `
      <span class="md-body-small" title="${escapeHtml(videoName)}"
            style="flex:0 0 28%; min-width:0; overflow:hidden;
                   text-overflow:ellipsis; white-space:nowrap;">
        ${escapeHtml(videoName)}
      </span>
      <span class="md-chip" style="background:var(--md-sys-color-secondary-container);
                                   color:var(--md-sys-color-on-secondary-container);
                                   font-size:12px; padding:2px 8px; border-radius:8px;
                                   white-space:nowrap; flex-shrink:0;">
        ${escapeHtml(role)}${lockHtml}
      </span>
      <input type="text" readonly value="${escapeHtml(sl.url)}"
             style="flex:1; min-width:0; font-size:11px; font-family:monospace; padding:4px 8px;
                    border:1px solid var(--md-sys-color-outline-variant); border-radius:4px;
                    background:var(--md-sys-color-surface-container);
                    color:var(--md-sys-color-on-surface); cursor:pointer;"
             onclick="this.select(); document.execCommand('copy');" title="Click to copy">
      <button class="md-icon-button" style="color:var(--md-sys-color-error); flex-shrink:0;"
              onclick="deleteVideoShareLink('${escapeHtml(sl.token)}')" title="Delete link">
        <span class="material-symbols-outlined" style="font-size:18px;">delete</span>
      </button>`;
    tbody.appendChild(row);

    // Reveal the table container and hide the empty-state message.
    const tableWrap = document.getElementById('gallery-share-table');
    if (tableWrap) tableWrap.style.display = '';
    const emptyEl = document.getElementById('gallery-share-empty');
    if (emptyEl) emptyEl.style.display = 'none';

    _refreshGalleryShareCount();
}

// Recompute the "N link(s)" label in the <summary> tag from current DOM.
function _refreshGalleryShareCount() {
    const tbody = document.getElementById('gallery-share-tbody');
    if (!tbody) return;
    const n = tbody.querySelectorAll('[data-token]').length;
    const countEl = document.getElementById('gallery-share-count');
    if (countEl) countEl.textContent = `${n} link${n === 1 ? '' : 's'}`;
}

// Keep the embedded JSON inside the current card in sync with whatever we
// just rendered. Otherwise reopening the sidebar re-parses stale data and
// resurrects deleted links / loses freshly-created ones.
function _updateCardShareLinks(transform) {
    if (!_currentCard) return;
    const slEl = _currentCard.querySelector('.vsl-data');
    if (!slEl) return;
    let arr = [];
    try { arr = JSON.parse(slEl.textContent || '[]'); } catch (_) {}
    slEl.textContent = JSON.stringify(transform(arr));
}

function _vslShowError(msg) {
    const c = document.getElementById('video-share-links');
    if (c) {
        const span = document.createElement('span');
        span.className = 'md-body-small';
        span.style.color = 'var(--md-sys-color-error)';
        span.textContent = msg;
        c.appendChild(span);
    }
    console.error('[Share]', msg);
}

async function createVideoShareLink() {
    if (!_videoShareCreateUrl) {
        _vslShowError('No URL — try refreshing the page.');
        return;
    }
    const btn = document.getElementById('vsl-generate-btn');
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
            _vslShowError(msg);
            return;
        }

        let data;
        try {
            data = await resp.json();
        } catch (e) {
            _vslShowError('Unexpected server response (not JSON).');
            console.error('[Share] JSON parse error', e);
            return;
        }

        const pw = document.getElementById('vsl-password');
        if (pw) pw.value = '';

        _appendVideoShareLink(data);
    } catch (e) {
        _vslShowError('Network error — check the browser console.');
        console.error('[Share] fetch error', e);
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function deleteVideoShareLink(token) {
    if (!token || !_shareLinkDeleteBase) return;
    const deleteUrl = _shareLinkDeleteBase.replace(_PLACEHOLDER, token);
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
            // Remove the row directly from the DOM
            const container = document.getElementById('video-share-links');
            if (container) {
                const row = container.querySelector(`[data-token="${token}"]`);
                if (row) row.remove();
                if (!container.querySelector('[data-token]')) {
                    container.innerHTML = '<span class="md-body-small text-on-surface-variant vsl-empty">No share links yet.</span>';
                }
            }
            // Drop the entry from the card's embedded JSON so the next sidebar
            // open doesn't resurrect the deleted link.
            _updateCardShareLinks(arr => arr.filter(x => x.token !== token));
            // Also drop any gallery-level row showing the same link.
            const galleryRow = document.querySelector(`#gallery-share-table [data-token="${token}"]`);
            if (galleryRow) galleryRow.remove();
            const tbody = document.getElementById('gallery-share-tbody');
            if (tbody && !tbody.children.length) {
                const empty = document.getElementById('gallery-share-empty');
                if (empty) empty.style.display = '';
                const tableWrap = document.getElementById('gallery-share-table');
                if (tableWrap) tableWrap.style.display = 'none';
            }
            _refreshGalleryShareCount();
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
// Generate button — document-level capture listener (most reliable)
// Fires before any element-level handler, catches clicks on the button
// or any child element (icon span, text) inside it.
// ---------------------------------------------------------------------------

document.addEventListener('click', function (e) {
    if (e.target.closest && e.target.closest('#vsl-generate-btn')) {
        e.preventDefault();
        e.stopPropagation();
        createVideoShareLink();
    }
}, true /* useCapture — runs before bubbling handlers */);

// Also wire directly at load time in case closest() is unavailable.
(function () {
    const btn = document.getElementById('vsl-generate-btn');
    if (btn) btn.addEventListener('click', createVideoShareLink);
}());

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

// ---------------------------------------------------------------------------
// Rename / Move / Bulk / Multi-select / View toggle
// ---------------------------------------------------------------------------

// --- Inline rename inside the sidebar ---
function startSidebarRename() {
    if (!_currentCard) return;
    const titleEl = document.getElementById('sidebar-title');
    if (!titleEl) return;
    const current = titleEl.textContent.trim();
    const input = document.createElement('input');
    input.type = 'text';
    input.value = current;
    input.maxLength = 255;
    input.className = 'admin-compact-input';
    input.style.cssText = 'flex:1; min-width:0; font-size:16px;';
    titleEl.replaceWith(input);
    input.focus();
    input.select();

    let done = false;
    const cancel = () => {
        if (done) return; done = true;
        const span = document.createElement('span');
        span.className = 'title'; span.id = 'sidebar-title';
        span.textContent = current;
        input.replaceWith(span);
    };
    const commit = async () => {
        if (done) return;
        const newName = input.value.trim();
        if (!newName || newName === current) { cancel(); return; }
        done = true;
        input.disabled = true;
        const url = _renameVideoBase.replace(_PLACEHOLDER, _currentVideoId);
        try {
            const resp = await fetch(url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken(),
                },
                body: JSON.stringify({ name: newName }),
            });
            if (!resp.ok) {
                alert(`Rename failed (${resp.status}).`);
                input.disabled = false;
                done = false;
                return;
            }
            const data = await resp.json();
            const finalName = data.name || newName;
            // Restore the span
            const span = document.createElement('span');
            span.className = 'title'; span.id = 'sidebar-title';
            span.textContent = finalName;
            input.replaceWith(span);
            // Update the card too
            if (_currentCard) {
                _currentCard.dataset.videoName = finalName;
                const titleNode = _currentCard.querySelector('.video-title');
                if (titleNode) titleNode.textContent = finalName;
                const fname = document.getElementById('sidebar-filename');
                if (fname) fname.textContent = finalName;
            }
        } catch (e) {
            alert('Network error while renaming.');
            input.disabled = false;
            done = false;
        }
    };

    input.addEventListener('blur', commit);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); commit(); }
        else if (e.key === 'Escape') { e.preventDefault(); cancel(); }
    });
}

// --- View toggle (grid / list) ---
function setViewMode(mode) {
    const container = document.getElementById('videos-container');
    if (!container) return;
    if (mode === 'list') {
        container.classList.remove('video-grid');
        container.classList.add('video-list');
    } else {
        container.classList.remove('video-list');
        container.classList.add('video-grid');
        mode = 'grid';
    }
    const g = document.getElementById('view-grid-btn');
    const l = document.getElementById('view-list-btn');
    if (g) g.classList.toggle('active', mode === 'grid');
    if (l) l.classList.toggle('active', mode === 'list');
    try { localStorage.setItem('vpm.viewMode', mode); } catch (_) {}
}

(function _restoreViewMode() {
    let mode = 'grid';
    try { mode = localStorage.getItem('vpm.viewMode') || 'grid'; } catch (_) {}
    // Wait for DOM ready if needed.
    if (document.getElementById('videos-container')) setViewMode(mode);
    else document.addEventListener('DOMContentLoaded', () => setViewMode(mode));
})();

// --- Multi-select ---
function toggleSelectMode() {
    _selectMode = !_selectMode;
    document.body.classList.toggle('select-mode', _selectMode);
    const label = document.getElementById('select-mode-label');
    if (label) label.textContent = _selectMode ? 'Done' : 'Select';
    if (!_selectMode) clearSelection();
    _updateBulkBar();
}

function onSelectionChange(e) {
    const id = e.target.dataset.videoId;
    if (e.target.checked) _selectedIds.add(id);
    else _selectedIds.delete(id);
    _updateBulkBar();
}

function clearSelection() {
    _selectedIds.clear();
    document.querySelectorAll('.vid-select-cb').forEach((cb) => { cb.checked = false; });
    _updateBulkBar();
}

function _updateBulkBar() {
    const bar = document.getElementById('bulk-action-bar');
    if (!bar) return;
    const count = _selectedIds.size;
    bar.style.display = (_selectMode && count > 0) ? 'flex' : 'none';
    const countEl = document.getElementById('bulk-selected-count');
    if (countEl) countEl.textContent = String(count);
}

function confirmBulkDelete() {
    const dialog = document.getElementById('bulk-delete-dialog');
    if (!dialog) return;
    const countEl = document.getElementById('bulk-delete-count');
    if (countEl) countEl.textContent = String(_selectedIds.size);
    dialog.classList.add('open');
}

async function executeBulkDelete() {
    const dialog = document.getElementById('bulk-delete-dialog');
    if (!_bulkDeleteUrl || _selectedIds.size === 0) {
        if (dialog) dialog.classList.remove('open');
        return;
    }
    try {
        const resp = await fetch(_bulkDeleteUrl, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({ video_ids: [..._selectedIds] }),
        });
        if (!resp.ok) {
            alert(`Bulk delete failed (${resp.status}).`);
            return;
        }
        // Remove deleted cards from DOM.
        for (const id of _selectedIds) {
            const card = document.querySelector(`.video-card[data-video-id="${id}"]`);
            if (card) card.remove();
        }
        clearSelection();
    } catch (e) {
        alert('Network error during bulk delete.');
    } finally {
        if (dialog) dialog.classList.remove('open');
    }
}

// --- Move dialog ---
async function _loadGalleries() {
    if (_galleryPickerCache) return _galleryPickerCache;
    if (!_galleryPickerUrl) return [];
    try {
        const resp = await fetch(_galleryPickerUrl, {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' },
        });
        if (!resp.ok) return [];
        const data = await resp.json();
        _galleryPickerCache = data.galleries || [];
        return _galleryPickerCache;
    } catch (e) {
        return [];
    }
}

// Renders the gallery list into the move dialog. `mode` is "single" or "bulk".
async function _renderMoveTargets(mode) {
    const list = document.getElementById('move-targets');
    if (!list) return;
    list.innerHTML = '<span class="md-body-small text-on-surface-variant">Loading galleries…</span>';
    const galleries = await _loadGalleries();
    const filter = (document.getElementById('move-filter')?.value || '').toLowerCase();
    const excludeGalleryId = (mode === 'single')
        ? _currentGalleryPk
        : null; // bulk: don't exclude — the API will skip per-video duplicates

    const shown = galleries.filter((g) => {
        if (excludeGalleryId && g.gallery_id === excludeGalleryId) return false;
        if (!filter) return true;
        return (g.project_name + ' / ' + g.gallery_name).toLowerCase().includes(filter);
    });

    if (!shown.length) {
        list.innerHTML = '<span class="md-body-small text-on-surface-variant">No galleries match.</span>';
        return;
    }

    list.innerHTML = shown.map((g) => `
        <button type="button" class="md-button-text" data-gallery-id="${escapeHtml(g.gallery_id)}"
                style="justify-content:flex-start; padding:10px 12px; text-align:left; gap:8px;">
          <span class="material-symbols-outlined" style="font-size:18px; color:var(--md-sys-color-primary);">photo_library</span>
          <span style="display:flex; flex-direction:column; align-items:flex-start; line-height:1.2;">
            <span class="md-body-medium">${escapeHtml(g.gallery_name)}</span>
            <span class="md-body-small text-on-surface-variant">${escapeHtml(g.project_name)}</span>
          </span>
        </button>
    `).join('');

    list.querySelectorAll('button[data-gallery-id]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const targetGalleryId = btn.dataset.galleryId;
            if (mode === 'single') _moveCurrentVideo(targetGalleryId);
            else _moveSelected(targetGalleryId);
        });
    });
}

// Open the move dialog for the video currently shown in the sidebar.
function openMoveDialogForCurrentVideo() {
    if (!_currentVideoId) return;
    const dialog = document.getElementById('move-dialog');
    if (!dialog) return;
    const countEl = document.getElementById('move-dialog-count');
    if (countEl) countEl.textContent = 'video';
    const filterEl = document.getElementById('move-filter');
    if (filterEl) {
        filterEl.value = '';
        filterEl.oninput = () => _renderMoveTargets('single');
    }
    dialog.classList.add('open');
    _renderMoveTargets('single');
}

// Open the move dialog for the currently-selected videos (bulk mode).
function openMoveDialog() {
    if (_selectedIds.size === 0) return;
    const dialog = document.getElementById('move-dialog');
    if (!dialog) return;
    const countEl = document.getElementById('move-dialog-count');
    if (countEl) countEl.textContent = `${_selectedIds.size} videos`;
    const filterEl = document.getElementById('move-filter');
    if (filterEl) {
        filterEl.value = '';
        filterEl.oninput = () => _renderMoveTargets('bulk');
    }
    dialog.classList.add('open');
    _renderMoveTargets('bulk');
}

async function _moveCurrentVideo(targetGalleryId) {
    if (!_currentVideoId || !_moveVideoBase) return;
    const url = _moveVideoBase.replace(_PLACEHOLDER, _currentVideoId);
    try {
        const resp = await fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({ target_gallery_id: targetGalleryId }),
        });
        if (!resp.ok) {
            let msg = `Move failed (${resp.status}).`;
            try { const j = await resp.json(); msg = j.error || msg; } catch (_) {}
            alert(msg);
            return;
        }
        // Remove the card from the current page and close everything.
        if (_currentCard) _currentCard.remove();
        document.getElementById('move-dialog')?.classList.remove('open');
        closeSidebar();
    } catch (e) {
        alert('Network error during move.');
    }
}

async function _moveSelected(targetGalleryId) {
    if (_selectedIds.size === 0 || !_bulkMoveUrl) return;
    const ids = [..._selectedIds];
    try {
        const resp = await fetch(_bulkMoveUrl, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({ video_ids: ids, target_gallery_id: targetGalleryId }),
        });
        if (!resp.ok) {
            let msg = `Bulk move failed (${resp.status}).`;
            try { const j = await resp.json(); msg = j.error || msg; } catch (_) {}
            alert(msg);
            return;
        }
        for (const id of ids) {
            const card = document.querySelector(`.video-card[data-video-id="${id}"]`);
            if (card) card.remove();
        }
        clearSelection();
        document.getElementById('move-dialog')?.classList.remove('open');
    } catch (e) {
        alert('Network error during bulk move.');
    }
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
window.startSidebarRename    = startSidebarRename;
window.openMoveDialog        = openMoveDialog;
window.openMoveDialogForCurrentVideo = openMoveDialogForCurrentVideo;
window.setViewMode           = setViewMode;
window.toggleSelectMode      = toggleSelectMode;
window.onSelectionChange     = onSelectionChange;
window.clearSelection        = clearSelection;
window.confirmBulkDelete     = confirmBulkDelete;
window.executeBulkDelete     = executeBulkDelete;
