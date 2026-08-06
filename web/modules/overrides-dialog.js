/** The saved-overrides manager: list, delete, clear and export. */

import { api } from "../../../scripts/api.js";

import { $el } from "./dom.js";
import { escapeHtml, notifyError } from "./util.js";

export class ManageOverridesDialog {
    constructor() {
        this.data = null;
        this.search = '';
        this.element = $el("div.comfy-modal", {
            id: 'manage-overrides-modal',
            parent: document.body,
            style: {
                position: "fixed",
                top: "50%",
                left: "50%",
                transform: "translate(-50%, -50%)",
                width: "720px",
                height: "520px",
                maxWidth: "95vw",
                maxHeight: "90vh",
                backgroundColor: "var(--comfy-menu-bg, #202020)",
                color: "var(--input-text, #ffffff)",
                border: "2px solid var(--border-color, #555555)",
                borderRadius: "8px",
                padding: "0",
                zIndex: "99999",
                boxShadow: "0 4px 20px rgba(0,0,0,0.8)",
                display: "none",
                flexDirection: "column",
                resize: "both",
                overflow: "hidden",
                minWidth: "560px",
                minHeight: "360px"
            }
        }, [
            this._header(),
            this._content(),
            this._footer()
        ]);

        // Persist window size for overrides dialog
        try {
            const saved = localStorage.getItem('model_linker_overrides_size');
            if (saved) {
                const { w, h } = JSON.parse(saved);
                if (w && h) { this.element.style.width = `${w}px`; this.element.style.height = `${h}px`; }
            }
            if (window.ResizeObserver) {
                const ro = new ResizeObserver((entries) => {
                    for (const entry of entries) {
                        const rect = entry.target.getBoundingClientRect();
                        localStorage.setItem('model_linker_overrides_size', JSON.stringify({ w: Math.round(rect.width), h: Math.round(rect.height) }));
                    }
                });
                ro.observe(this.element);
                this._ro = ro;
            }
        } catch (e) { }

        // Scoped smaller button font-size (2px less) in overrides modal
        try {
            if (!document.getElementById('manage-overrides-style-buttons')) {
                const style = $el('style', {
                    id: 'manage-overrides-style-buttons', textContent: `
                    #manage-overrides-modal .model-linker-resolve-btn,
                    #manage-overrides-modal .comfy-button { font-size: calc(1em - 2px); }
                `});
                document.head.appendChild(style);
            }
        } catch (e) { }
    }

    _header() {
        return $el("div", {
            style: {
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "12px 16px",
                borderBottom: "1px solid var(--border-color)"
            }
        }, [
            $el("h2", { textContent: "Manage Overrides", style: { margin: 0, fontSize: '16px' } }),
            $el("button", { textContent: "×", onclick: () => this.close(), style: { background: 'none', border: 'none', fontSize: '20px', cursor: 'pointer', color: 'var(--input-text)' } })
        ]);
    }

    _content() {
        this.contentEl = $el("div", {
            style: { padding: '12px', overflow: 'auto', flex: '1', minHeight: 0 }
        }, [
            $el("div", { style: { display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '8px' } }, [
                $el("input", {
                    id: 'ovr-search',
                    placeholder: 'search overrides...',
                    oninput: (e) => { this.search = e.target.value || ''; this.renderList(); },
                    style: { flex: 1, padding: '6px' }
                }),
                $el("span", { id: 'ovr-count', textContent: '' })
            ]),
            // Header row for two columns
            $el('div', { style: { display: 'flex', gap: '8px', padding: '6px 8px', borderBottom: '1px solid var(--border-color)', fontWeight: 600, opacity: .9 } }, [
                $el('div', { style: { flex: '2 1 40%' }, textContent: 'Missing / Category' }),
                $el('div', { style: { flex: '3 1 55%' }, textContent: 'Override Path' }),
                $el('div', { style: { flex: '0 0 auto', width: '72px', textAlign: 'right' }, textContent: 'Actions' })
            ]),
            this.listEl = $el("div", { id: 'ovr-list', style: { display: 'flex', flexDirection: 'column', gap: '6px' } })
        ]);
        return this.contentEl;
    }

    _footer() {
        // Import
        const fileInput = $el('input', { type: 'file', accept: '.json', style: { display: 'none' } });
        const importBtn = $el('button', { className: 'model-linker-resolve-btn', textContent: 'Import JSON', onclick: () => fileInput.click(), style: { padding: '6px 10px' } });
        fileInput.addEventListener('change', async (e) => {
            const f = e.target.files && e.target.files[0];
            if (!f) return;
            try {
                const text = await f.text();
                const json = JSON.parse(text);
                const resp = await api.fetchApi('/model_linker/overrides/replace', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ overrides: json }) });
                const data = await resp.json();
                if (data.success) {
                    await this.load();
                } else {
                    notifyError('Import failed', new Error(data.error || 'unknown'));
                }
            } catch (err) {
                notifyError('Invalid JSON', err);
            } finally {
                e.target.value = '';
            }
        });

        return $el("div", { style: { padding: '10px 12px', borderTop: '1px solid var(--border-color)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' } }, [
            $el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } }, [
                $el('button', { className: 'model-linker-resolve-btn', textContent: 'Export JSON', onclick: () => this.export(), style: { padding: '6px 10px' } }),
                importBtn,
                fileInput
            ]),
            $el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } }, [
                $el('button', { className: 'model-linker-resolve-btn', textContent: 'Clear All', onclick: () => this.clearAll(), style: { padding: '6px 10px' } })
            ])
        ]);
    }

    async show() {
        this.element.style.display = 'flex';
        await this.load();
    }

    close() { this.element.style.display = 'none'; }

    async load() {
        try {
            const resp = await api.fetchApi('/model_linker/overrides');
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            this.data = await resp.json();
            this.renderList();
        } catch (e) {
            console.error('Overrides load error', e);
            if (this.listEl) this.listEl.textContent = 'Error loading overrides';
        }
    }

    renderList() {
        if (!this.listEl) return;
        const mappings = (this.data && this.data.overrides && this.data.overrides.mappings) || [];
        const q = (this.search || '').toLowerCase();
        const filtered = mappings.filter(m => {
            const s = `${m.key || ''} ${m.original_filename || ''} ${m.category || ''} ${m.path || ''}`.toLowerCase();
            return !q || s.includes(q);
        });
        const countEl = this.contentEl && this.contentEl.querySelector('#ovr-count');
        if (countEl) countEl.textContent = `${filtered.length}/${mappings.length}`;
        if (!filtered.length) {
            this.listEl.innerHTML = '<div style="opacity:0.8; padding:8px;">No overrides</div>';
            return;
        }
        let html = '';
        for (const m of filtered) {
            const delId = `ovr-del-${m.key}`.replace(/[^a-zA-Z0-9_-]/g, '_');
            // An override records the name a workflow asked for, so these are
            // workflow-supplied values that happen to have been stored on the
            // way through. Escaped for the same reason as everywhere else.
            const original = escapeHtml(m.original_filename || '');
            const category = escapeHtml(m.category || 'any');
            const fullPath = escapeHtml(m.path || '');
            const leaf = escapeHtml((m.path || '').split(/[\\/]/).pop() || '');
            html += `<div style="border:1px solid var(--border-color); border-radius:4px; padding:8px; display:flex; flex-direction:column; gap:6px;">
                <div style="display:flex; align-items:center; justify-content:space-between; gap:8px;">
                    <div style="flex:1 1 auto; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"><code title="${original}">${original}</code> <span style="opacity:.8;">[${category}]</span></div>
                    <div style="flex:0 0 auto;"><button id="${delId}" class="model-linker-resolve-btn" style="padding:4px 8px;">Delete</button></div>
                </div>
                <div style="height:1px; background: var(--border-color); opacity:.5;"></div>
                <div style="display:flex; align-items:center; justify-content:space-between; gap:8px;">
                    <div style="flex:1 1 auto; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"><code title="${leaf}">${leaf}</code></div>
                    <div style="flex:0 0 auto;"><button id="ovr-pathbtn-${(m.key || '').replace(/[^a-zA-Z0-9_-]/g, '_')}" class="model-linker-resolve-btn" style="padding:4px 8px;">Path</button></div>
                </div>
                <div id="ovr-pathrow-${(m.key || '').replace(/[^a-zA-Z0-9_-]/g, '_')}" style="display:none; overflow-wrap:anywhere; opacity:.9;"><code title="${fullPath}">${fullPath}</code></div>
            </div>`;
        }
        this.listEl.innerHTML = html;
        // Wire delete and path toggle
        for (const m of filtered) {
            const safeKey = (m.key || '').replace(/[^a-zA-Z0-9_-]/g, '_');
            const delId = `ovr-del-${m.key}`.replace(/[^a-zA-Z0-9_-]/g, '_');
            const btn = this.listEl.querySelector(`#${delId}`);
            if (btn) {
                btn.addEventListener('click', async () => {
                    try {
                        const resp = await api.fetchApi('/model_linker/overrides/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: m.key }) });
                        const data = await resp.json();
                        if (data.success) await this.load(); else notifyError('Delete failed', new Error(data.error || 'unknown'));
                    } catch (e) { notifyError('Delete failed', e); }
                });
            }
            const pathBtn = this.listEl.querySelector(`#ovr-pathbtn-${safeKey}`);
            const pathRow = this.listEl.querySelector(`#ovr-pathrow-${safeKey}`);
            if (pathBtn && pathRow) {
                pathBtn.addEventListener('click', () => {
                    const vis = pathRow.style.display !== 'none';
                    pathRow.style.display = vis ? 'none' : 'block';
                    pathBtn.textContent = vis ? 'Path' : 'Hide path';
                });
            }
        }
    }

    async clearAll() {
        try {
            const resp = await api.fetchApi('/model_linker/overrides/clear', { method: 'POST' });
            const data = await resp.json();
            if (data.success) await this.load(); else notifyError('Clear failed', new Error(data.error || 'unknown'));
        } catch (e) { notifyError('Clear failed', e); }
    }

    export() {
        try {
            const doc = (this.data && this.data.overrides) || { version: 1, mappings: [] };
            const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'overrides.json';
            document.body.appendChild(a);
            a.click();
            setTimeout(() => { URL.revokeObjectURL(url); document.body.removeChild(a); }, 0);
        } catch (e) { console.error('Export error', e); }
    }
}
