'use strict';

(function() {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;

    // Elements
    const startStopInput = document.getElementById('keybind-start-stop-input');
    const discardInput = document.getElementById('keybind-discard-input');
    const saveBtn = document.getElementById('save-keybinds-btn');
    const statusEl = document.getElementById('keybind-status');

    if (!startStopInput || !discardInput) return;

    let currentKeybinds = {
        start_stop_key: startStopInput.value || 'Space',
        discard_key: discardInput.value || 'Escape'
    };

    // Capture key press for keybind inputs
    function setupKeyCapture(input, keybindName) {
        input.addEventListener('keydown', function(e) {
            e.preventDefault();
            e.stopPropagation();
            const keyCode = e.code;
            input.value = keyCode;
            currentKeybinds[keybindName] = keyCode;
            input.classList.add('captured');
            setTimeout(() => input.classList.remove('captured'), 300);
        });

        input.addEventListener('focus', function() {
            input.placeholder = 'Press a key...';
        });

        input.addEventListener('blur', function() {
            input.placeholder = '';
        });
    }

    setupKeyCapture(startStopInput, 'start_stop_key');
    setupKeyCapture(discardInput, 'discard_key');

    // Save keybinds
    if (saveBtn) {
        saveBtn.addEventListener('click', async function() {
            saveBtn.disabled = true;
            try {
                const resp = await fetch('/recording/keybinds/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken,
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    body: JSON.stringify(currentKeybinds)
                });

                if (resp.ok) {
                    if (statusEl) {
                        statusEl.textContent = 'Saved!';
                        statusEl.className = 'md-label-medium success';
                        setTimeout(() => { statusEl.textContent = ''; }, 2000);
                    }
                } else {
                    throw new Error('Save failed');
                }
            } catch (err) {
                if (statusEl) {
                    statusEl.textContent = 'Error saving keybinds';
                    statusEl.className = 'md-label-medium error';
                }
            } finally {
                saveBtn.disabled = false;
            }
        });
    }

    // Load current keybinds
    async function loadKeybinds() {
        try {
            const resp = await fetch('/recording/keybinds/', {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });
            if (resp.ok) {
                const data = await resp.json();
                currentKeybinds = data;
                if (startStopInput) startStopInput.value = data.start_stop_key || 'Space';
                if (discardInput) discardInput.value = data.discard_key || 'Escape';
            }
        } catch (err) {
            console.error('Failed to load keybinds:', err);
        }
    }

    loadKeybinds();
})();
