/* Model Management Page Specific JavaScript */

/* Helper function to escape HTML */
function esc(str) {
    return ('' + str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

/* AJAX helper function */
async function api(endpoint, options = {}) {
    try {
        const resp = await fetch(endpoint, {
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                ...(options.headers || {})
            },
            ...options
        });
        if (!resp.ok) {
            const error = await resp.text();
            throw new Error(error || resp.statusText);
        }
        return await resp.json();
    } catch (e) {
        throw e;
    }
}

/* Toast notification function */
function toast(message, type = 'info') {
    // Reuse the toast function from main app.js if available
    if (window.toast) {
        window.toast(message, type);
        return;
    }

    // Fallback implementation
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('show');
    }, 100);

    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

/* Model management functions */

/* Load models from backend API */
async function loadModels() {
    const ml = document.getElementById('modelList');
    const rl = document.getElementById('recommendedList');

    if (!ml || !rl) return;

    try {
        const r = await api('/api/models');

        // Generate HTML for installed models
        let installedHtml = '';
        for (const m of r.models) {
            const displaySize = m.size || 'Unknown';
            const displayFormat = m.file ? (m.file.toUpperCase().endsWith('.GGUF') ? 'GGUF' : 'Unknown') : 'GGUF';

            // Determine if this model is active based on its type and the active paths from the backend
            const act = m.active ? '<span class="badge badge-accent">ACTIVE</span>' : '';

            // Prepare buttons based on model type
            const btns = [];
            if (m.type === 'generation') {
                // Generation model actions
                if (m.present && m.valid && !m.active) {
                    btns.push('<button class="btn btn-sm btn-primary" onclick="switchModel(\'' + esc(m.file) + '\',\'' + esc(m.name) + '\')">⇄ Switch</button>');
                }
                if (m.present) {
                    btns.push('<button class="btn btn-sm btn-danger" onclick="deleteModel(\'' + esc(m.file) + '\', \'generation\')">🗑 Delete</button>');
                }
                if (m.present && !m.valid) {
                    btns.push('<button class="btn btn-sm" onclick="redownloadModel(\'' + esc(m.file) + '\', \'generation\')">↻ Re-download</button>');
                }
                if (!m.present) {
                    btns.push('<button class="btn btn-sm btn-primary" onclick="startDownload(\'' + esc(m.file) + '\',\'' + esc(m.url) + '\',\'' + esc(m.name) + '\', \'generation\')">⬇ Download</button>');
                }
            } else if (m.type === 'embedding') {
                // Embedding model actions: no switch (requires restart and re-indexing)
                if (m.present) {
                    btns.push('<button class="btn btn-sm btn-danger" onclick="deleteModel(\'' + esc(m.file) + '\', \'embedding\')" title="Deleting the embedding model requires restarting the embedding server and may require re-indexing semantic memory.">🗑 Delete</button>');
                }
                if (m.present && !m.valid) {
                    btns.push('<button class="btn btn-sm" onclick="redownloadModel(\'' + esc(m.file) + '\', \'embedding\')">↻ Re-download</button>');
                }
                if (!m.present) {
                    btns.push('<button class="btn btn-sm btn-primary" onclick="startDownload(\'' + esc(m.file) + '\',\'' + esc(m.url) + '\',\'' + esc(m.name) + '\', \'embedding\')">⬇ Download</button>');
                }
            }

            // Show type badge
            const typeBadge = m.type === 'generation' ? '<span class="badge badge-info">GEN</span>' : '<span class="badge badge-purple">EMB</span>';

            installedHtml += '<div class="mcard"><div class="mcard-header">' +
                '<div class="mcard-title">' + esc(m.name) + ' ' + act + ' ' + typeBadge + '</div>' +
                '<div class="mcard-subtitle">' + displaySize + ' ' + displayFormat + '</div>' +
                '</div><div class="mcard-actions">' + btns.join('') + '</div></div>';
        }

        // For the recommended list, we only show generation models that are not custom and not present
        const recs = r.models.filter(m => m.type === 'generation' && !m.custom && !m.present);
        let recHtml = '';
        if (recs.length) {
            recHtml = recs.map((mn) => {
                // Parse filename for recommended models too
                let recSizeInfo = mn.size || '';
                let recFormatInfo = '';
                if (mn.file) {
                    const match = mn.file.match(/(\d+[bm])\b/i);
                    if (match) recSizeInfo = match[1].toUpperCase() + (match[1].endsWith('b') ? 'B' : '');
                    const quantMatch = mn.file.match(/(_Q\d+[_K]?_[\w]+)\.gguf$/i);
                    if (quantMatch) recFormatInfo = quantMatch[1].toUpperCase();
                }
                const displaySize = recSizeInfo || (mn.size || '');
                const displayFormat = recFormatInfo || 'GGUF';
                return '<div class="mcard" style="background:var(--surface)"><div class="mcard-header">' +
                    '<div class="mcard-title">' + esc(mn.name) + '</div>' +
                    '<div class="mcard-subtitle">' + displaySize + ' ' + displayFormat + '</div>' +
                    '</div><div class="mcard-actions"><button class="btn btn-sm btn-primary" onclick="startDownload(\'' + esc(mn.file) + '\',\'' + esc(mn.url) + '\',\'' + esc(mn.name) + '\', \'generation\')">⬇ Download</button></div></div>';
            }).join('');
        } else {
            recHtml = '<div class="dim" style="padding:8px 0;font-size:12px">All recommended generation models are installed.</div>';
        }

        ml.innerHTML = installedHtml || '<div class="empty-state"><div class="es-title">No models on disk</div><div class="es-desc">Download from Recommended below, or use Custom Download.</div></div>';
        rl.innerHTML = recHtml;
    } catch (e) {
        if (ml) ml.innerHTML = '<div class="empty-state"><div class="es-title">Error loading models</div><div class="es-desc">' + esc(e.message) + '</div></div>';
        if (rl) rl.innerHTML = '<div class="dim" style="padding:8px 0;font-size:12px">Error loading recommended models</div>';
        toast('Failed to load models: ' + e.message, 'err');
    }
}

/* Download model */
async function startDownload(file, url, name, type = 'generation') {
    if (window._dlPoll) { toast('Download in progress', 'info'); return; }
    try {
        const r = await api('/api/models/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file, url, name, type }),
        });
        if (r.error) { toast(r.error, 'err'); return; }
        toast('Downloading: ' + name, 'info');
        pollDownload();
    } catch (e) { toast(e.message, 'err'); }
}

/* Redownload model */
async function redownloadModel(file, type = 'generation') {
    if (window._dlPoll) { toast('Download in progress', 'info'); return; }
    try {
        const resp = await api('/api/models/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file, url: '', force: true, type }),
        });
        if (!resp.ok) {
            toast(resp.error || resp.statusText, 'err');
            return;
        }
        toast('Re-downloading: ' + file, 'info');
        pollDownload();
    } catch (e) { toast(e.message, 'err'); }
}

/* Delete model */
async function deleteModel(file, type = 'generation') {
    try {
        await api('/api/models/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file, type }),
        });
        toast('Deleted: ' + file);
        loadModels();
    } catch (e) { toast(e.message, 'err'); }
}

/* Switch model (generation only) */
async function switchModel(file, name) {
    try {
        await api('/api/models/switch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file, name }),
        });
        toast('Switched to ' + name);
        // Update dashboard immediately if we're still on main page
        const modelEl = document.getElementById('dash-model');
        if (modelEl) modelEl.textContent = name;
        setTimeout(loadModels, 4000);
    } catch (e) { toast(e.message, 'err'); }
}

/* Poll download status */
function pollDownload() {
    const dp = document.getElementById('dlProgress');
    if (dp) dp.style.display = 'block';
    const bar = document.getElementById('dlBarFill');
    const txt = document.getElementById('dlText');
    window._dlPoll = setInterval(async () => {
        try {
            const s = await api('/api/models/download_progress');
            let pct = 0;
            if (s.progress && s.progress.includes('%')) {
                const m = s.progress.match(/(\d+\.?\d*)%/);
                if (m) pct = parseFloat(m[1]);
            }
            if (bar) bar.style.width = Math.min(pct, 99) + '%';
            if (txt) {
                if (s.error) {
                    txt.textContent = '✗ ' + s.error;
                    txt.style.color = 'var(--accent4)';
                    if (bar) bar.style.background = 'var(--accent4)';
                }
                else if (s.progress === 'done') {
                    txt.textContent = '✓ Done!';
                    txt.style.color = 'var(--accent3)';
                    if (bar) {
                        bar.style.width = '100%';
                        bar.style.background = 'var(--accent3)';
                    }
                }
                else {
                    txt.textContent = String(s.progress).slice(-80);
                    txt.style.color = 'var(--yellow)';
                }
            }
            if (!s.active) {
                clearInterval(window._dlPoll);
                window._dlPoll = null;
                setTimeout(() => {
                    if (dp) dp.style.display = 'none';
                    loadModels();
                }, 2500);
            }
        } catch (e) { /* ignore */ }
    }, 1500);
}

/* Initialize page when loaded */
document.addEventListener('DOMContentLoaded', function() {
    loadModels();

    // Also try to update the main dashboard if we can access it
    // This is useful if someone navigates directly to this page
    try {
        const modelEl = window.parent ? window.parent.document.getElementById('dash-model') :
                       document.getElementById('dash-model');
        if (modelEl) {
            // Try to get active model from backend
            fetch('/api/dashboard')
                .then(r => r.json())
                .then(data => {
                    if (data.active_model) {
                        modelEl.textContent = data.active_model;
                    }
                });
        }
    } catch (e) {
        // Ignore errors - not critical
    }
});