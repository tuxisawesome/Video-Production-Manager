'use strict';

// ---------------------------------------------------------------------------
// Page data & CSRF
// ---------------------------------------------------------------------------

const pageData = JSON.parse(document.getElementById('page-data').textContent);

const NEXT_PAIR_URL = pageData.next_pair_url;
const SUBMIT_URL    = pageData.submit_url;
const CSRF_TOKEN    = pageData.csrf_token
    || document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')
    || '';

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------

const dom = {
    videoLeft:       document.getElementById('video-left'),
    videoRight:      document.getElementById('video-right'),
    nameLeft:        document.getElementById('name-left'),
    nameRight:       document.getElementById('name-right'),
    eloLeft:         document.getElementById('elo-left'),
    eloRight:        document.getElementById('elo-right'),
    btnLeftWins:     document.getElementById('btn-left-wins'),
    btnEqual:        document.getElementById('btn-equal'),
    btnRightWins:    document.getElementById('btn-right-wins'),
    progressBar:     document.getElementById('progress-bar'),
    progressDone:    document.getElementById('progress-done'),
    progressTotal:   document.getElementById('progress-total'),
    comparisonArea:  document.getElementById('comparison-area'),
    completionArea:  document.getElementById('completion-area'),
};

const actionButtons = [dom.btnLeftWins, dom.btnEqual, dom.btnRightWins];

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let currentPair = null;   // { video_left, video_right }
let isLoading   = false;

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function setButtonsDisabled(disabled) {
    for (const btn of actionButtons) {
        btn.disabled = disabled;
    }
}

function updateProgress(progress) {
    if (!progress) return;
    const { completed, total, percent } = progress;
    dom.progressDone.textContent  = completed;
    dom.progressTotal.textContent = total;
    dom.progressBar.style.width   = `${Math.min(100, Math.max(0, percent))}%`;
}

function showCompletion() {
    dom.comparisonArea.style.display  = 'none';
    dom.completionArea.style.display  = '';
}

function loadVideoIntoPlayer(videoEl, videoData) {
    videoEl.src = videoData.url;
    videoEl.load();
}

function populatePair(data) {
    const { video_left, video_right } = data;

    loadVideoIntoPlayer(dom.videoLeft, video_left);
    loadVideoIntoPlayer(dom.videoRight, video_right);

    dom.nameLeft.textContent  = video_left.name;
    dom.nameRight.textContent = video_right.name;
    dom.eloLeft.textContent   = `Elo: ${Math.round(video_left.elo)}`;
    dom.eloRight.textContent  = `Elo: ${Math.round(video_right.elo)}`;

    currentPair = { video_left, video_right };
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function fetchNextPair() {
    if (isLoading) return;
    isLoading = true;
    setButtonsDisabled(true);

    try {
        const response = await fetch(NEXT_PAIR_URL, {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
        });

        if (!response.ok) {
            throw new Error(`Failed to fetch next pair: ${response.status}`);
        }

        const data = await response.json();

        // Update progress regardless of completion status
        if (data.progress) {
            updateProgress(data.progress);
        }

        if (data.complete) {
            showCompletion();
            return;
        }

        populatePair(data);
    } catch (err) {
        console.error('[Ranking] fetchNextPair error:', err);
    } finally {
        isLoading = false;
        setButtonsDisabled(false);
    }
}

async function submitResult(result) {
    if (isLoading || !currentPair) return;
    isLoading = true;
    setButtonsDisabled(true);

    const body = {
        video_left:  currentPair.video_left.id,
        video_right: currentPair.video_right.id,
        result:      result,  // 'left' | 'right' | 'equal'
    };

    try {
        const response = await fetch(SUBMIT_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken':  CSRF_TOKEN,
            },
            body: JSON.stringify(body),
        });

        if (!response.ok) {
            throw new Error(`Submit failed: ${response.status}`);
        }

        // Pause current videos before loading next pair
        dom.videoLeft.pause();
        dom.videoRight.pause();

        // Fetch the next pair
        await fetchNextPair();
    } catch (err) {
        console.error('[Ranking] submitResult error:', err);
        isLoading = false;
        setButtonsDisabled(false);
    }
}

// ---------------------------------------------------------------------------
// Keyboard shortcuts
// ---------------------------------------------------------------------------

document.addEventListener('keydown', (e) => {
    if (isLoading || !currentPair) return;

    switch (e.key) {
        case 'ArrowLeft':
            e.preventDefault();
            submitResult('left');
            break;
        case 'ArrowUp':
        case 'ArrowDown':
            e.preventDefault();
            submitResult('equal');
            break;
        case 'ArrowRight':
            e.preventDefault();
            submitResult('right');
            break;
    }
});

// ---------------------------------------------------------------------------
// Expose submitResult globally for inline onclick handlers
// ---------------------------------------------------------------------------

window.submitResult = submitResult;

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

fetchNextPair();
