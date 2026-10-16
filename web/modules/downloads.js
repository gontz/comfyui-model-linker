/**
 * Downloads for missing models: what the background lookups found, starting
 * downloads, and their progress. Downloads run on the server, so closing the
 * dialog does not stop them; reopening it picks their progress back up.
 */

import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

import { escapeHtml, isPerfectMatch, notifyError, refSlot, safeHttpUrl } from "./util.js";

const ACTIVE = new Set(["queued", "starting", "downloading", "verifying"]);

const SOURCE_LABELS = {
    workflow: "the workflow's own link",
    hf_index: "HuggingFace (Comfy-Org / Kijai)",
    civitai_hash: "CivitAI, by hash",
    civarchive: "CivArchive, by hash",
    popular: "the curated list",
    model_list: "ComfyUI-Manager's list",
    huggingface: "HuggingFace",
    civitai: "CivitAI",
};

const basename = (path) => (path || "").split(/[\\/]/).pop();
const fileKey = (m) => `${m.category}|${basename(m.original_path).toLowerCase()}`;

export function formatBytes(n) {
    if (typeof n !== "number" || !isFinite(n) || n <= 0) return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

/** Finished means completed AND verified - the HF engine says "completed" before hashing. */
export function isFinished(record) {
    return record?.status === "completed" && !!record.sha256;
}

/** What a missing model's download block should show. Pure. */
export function downloadState(missing, record) {
    if (record && (ACTIVE.has(record.status) || (record.status === "completed" && !record.sha256))) {
        return { kind: "active" };
    }
    if (isFinished(record)) return { kind: "done" };
    if (record?.status === "error") return { kind: "error", message: record.error, auth: record.auth_required };
    if ((missing?.matches || []).some(isPerfectMatch)) return { kind: "none" };
    const url = safeHttpUrl(missing?.download_source?.url);
    if (url && url.startsWith("https://")) return { kind: "available", url, source: missing.download_source };
    if (missing?.lookup_state === "pending") return { kind: "searching" };
    if (missing?.lookup_state === "not_found") return { kind: "not_found" };
    return { kind: "idle" };
}

export class DownloadController {
    constructor(dialog) {
        this.dialog = dialog;
        this.container = null;
        this.missing = new Map();   // refSlot -> missing model
        this.byRef = new Map();     // refSlot -> download id
        this.records = {};          // download id -> progress record
        this.progressTimer = null;
        this.lookupTimer = null;
        // Guards re-arming a poll once the dialog has closed: a fetch already
        // in flight when close() runs still resolves and would otherwise
        // schedule the next timer on a hidden dialog, leaking it forever.
        this.live = false;
    }

    blockId(missing) {
        return `dl-${refSlot(missing)}`;
    }

    renderBlock(missing) {
        // Node and subgraph ids inside the slot come from the workflow file
        return `<div id="${escapeHtml(this.blockId(missing))}" style="margin: 4px 0 8px;">${this.blockHtml(missing)}</div>`;
    }

    blockHtml(missing) {
        const slot = refSlot(missing);
        const record = this.records[this.byRef.get(slot)];
        const state = downloadState(missing, record);
        // dataset.mlSlot hands back the unescaped slot, which is the map key
        const button = (action, text) =>
            `<button data-ml-action="${action}" data-ml-slot="${escapeHtml(slot)}" class="model-linker-resolve-btn" style="padding:2px 8px;">${text}</button>`;
        switch (state.kind) {
            case "active": {
                const label = record.status === "queued" ? "Queued"
                    : record.status === "verifying" ? "Verifying"
                    : `${formatBytes(record.downloaded) || "0 B"} / ${formatBytes(record.total_size) || "?"}`;
                const speed = record.speed ? ` · ${formatBytes(record.speed)}/s` : "";
                return `<div style="display:flex; gap:8px; align-items:center;">☁ ${escapeHtml(label)}`
                    + `<progress max="100" value="${Number(record.progress) || 0}" style="flex:1;"></progress>`
                    + `<span style="opacity:.7; font-size:12px;">${escapeHtml(speed)}</span>${button("cancel", "Cancel")}</div>`;
            }
            case "done":
                return `<div style="color:#0aa96e;">✓ Downloaded${record.metadata === "written" ? " · Lora Manager info written" : ""}</div>`;
            case "error":
                return `<div style="color:#e05050;">✗ ${escapeHtml(state.message || "Download failed")}</div>`
                    + (state.auth ? button("settings", "⚙ Settings") + " " : "") + button("download", "Retry");
            case "available": {
                const source = state.source || {};
                const where = SOURCE_LABELS[source.url_source] || SOURCE_LABELS[source.source] || source.source || "online";
                const size = typeof source.size === "number" ? formatBytes(source.size)
                    : (typeof source.size === "string" ? source.size : "");
                return `<div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">`
                    + button("download", `☁ Download${size ? ` (${escapeHtml(size)})` : ""}`)
                    + `<span style="opacity:.7; font-size:12px;">from ${escapeHtml(where)}</span>`
                    + `<a href="${escapeHtml(state.url)}" target="_blank" rel="noopener noreferrer" title="Open the source">↗</a></div>`;
            }
            case "searching":
                return `<div style="opacity:.7;">🔍 Looking for a download…</div>`;
            case "not_found":
                return `<div style="opacity:.7;">No download found online. ${button("search", "Search again")}</div>`;
            case "idle":
                return `<div>${button("search", "🔍 Find a download")}</div>`;
            default:
                return "";
        }
    }

    refresh(missing) {
        const element = this.container?.querySelector(`#${CSS.escape(this.blockId(missing))}`);
        if (element) element.innerHTML = this.blockHtml(missing);
    }

    afterRender(container, data) {
        this.live = true;
        this.container = container;
        this.missing = new Map((data.missing_models || []).map((m) => [refSlot(m), m]));
        if (!container._mlDownloadsWired) {
            container.addEventListener("click", (event) => this.onClick(event));
            container._mlDownloadsWired = true;
        }
        this.reconnect();
        if ((data.lookups_pending || 0) > 0) this.scheduleLookupPoll();
    }

    onClick(event) {
        const button = event.target.closest?.("[data-ml-action]");
        if (!button || !this.container?.contains(button)) return;
        const action = button.dataset.mlAction;
        if (action === "settings") { this.dialog.openSettings(); return; }
        const missing = this.missing.get(button.dataset.mlSlot);
        if (!missing) return;
        if (action === "download") this.start(missing);
        else if (action === "cancel") this.cancel(missing);
        else if (action === "search") this.searchAgain(missing);
    }

    async request(path, body) {
        const response = await api.fetchApi(path, body === undefined ? {} : {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data?.error || `HTTP ${response.status}`);
        return data;
    }

    expectedSha256(missing) {
        const source = missing.download_source || {};
        if (typeof source.sha256 === "string" && /^[0-9a-f]{64}$/i.test(source.sha256)) return source.sha256;
        const type = (missing.source_hash_type || "sha256").toLowerCase();
        return type === "sha256" && /^[0-9a-f]{64}$/i.test(missing.source_hash || "") ? missing.source_hash : null;
    }

    async start(missing) {
        const source = missing.download_source || {};
        const category = missing.category && missing.category !== "unknown" ? missing.category : source.directory;
        try {
            const data = await this.request("/model_linker/download", {
                url: source.url,
                category,
                filename: basename(missing.original_path),
                sha256: this.expectedSha256(missing),
                size: typeof source.size === "number" ? Math.round(source.size) : null,
            });
            // Every card referencing the same file follows this one download
            for (const other of this.missing.values()) {
                if (fileKey(other) === fileKey(missing)) this.track(other, data.download_id);
            }
            this.scheduleProgressPoll();
        } catch (error) {
            notifyError("Download did not start", error);
        }
    }

    track(missing, downloadId, record = { status: "queued", progress: 0 }) {
        this.byRef.set(refSlot(missing), downloadId);
        this.records[downloadId] = this.records[downloadId] || record;
        this.refresh(missing);
    }

    async cancel(missing) {
        const id = this.byRef.get(refSlot(missing));
        if (!id) return;
        try {
            await this.request(`/model_linker/cancel/${encodeURIComponent(id)}`, {});
        } catch (error) {
            notifyError("Could not cancel the download", error);
        }
    }

    scheduleProgressPoll() {
        if (this.live && !this.progressTimer) this.progressTimer = setTimeout(() => this.pollProgress(), 1000);
    }

    async pollProgress() {
        this.progressTimer = null;
        let all;
        try {
            all = await this.request("/model_linker/progress");
        } catch (error) {
            this.scheduleProgressPoll();
            return;
        }
        if (!this.live) return;
        let active = false;
        const finished = [];
        for (const [slot, id] of this.byRef) {
            const record = all[id];
            if (!record) continue;
            const before = this.records[id];
            this.records[id] = record;
            const missing = this.missing.get(slot);
            if (missing) this.refresh(missing);
            if (downloadState(missing, record).kind === "active") active = true;
            if (isFinished(record) && !isFinished(before) && !finished.includes(record)) finished.push(record);
        }
        if (active) this.scheduleProgressPoll();
        if (finished.length) await this.onFinished(finished);
    }

    async onFinished(records) {
        const names = [...new Set(records.map((r) => r.filename))].join(", ");
        app.extensionManager?.toast?.add({ severity: "success", summary: "Model Linker", detail: `Downloaded ${names}`, life: 5000 });
        try { await app.refreshComboInNodes?.(); } catch (e) { /* older frontends */ }
        await this.dialog.loadWorkflowData();
    }

    async reconnect() {
        let all;
        try {
            all = await this.request("/model_linker/progress");
        } catch (error) {
            return;
        }
        if (!this.live) return;
        let any = false;
        for (const [id, record] of Object.entries(all)) {
            if (downloadState(null, record).kind !== "active") continue;
            for (const missing of this.missing.values()) {
                if (basename(missing.original_path).toLowerCase() === (record.filename || "").toLowerCase()) {
                    this.records[id] = record;
                    this.track(missing, id, record);
                    any = true;
                }
            }
        }
        if (any) this.scheduleProgressPoll();
    }

    scheduleLookupPoll() {
        if (this.live && !this.lookupTimer) this.lookupTimer = setTimeout(() => this.pollLookups(), 2000);
    }

    async pollLookups() {
        this.lookupTimer = null;
        const waiting = [...this.missing.values()].filter((m) => m.lookup_state === "pending");
        if (!waiting.length) return;
        let data;
        try {
            data = await this.request("/model_linker/lookups", { keys: [...new Set(waiting.map((m) => m.lookup_key))] });
        } catch (error) {
            this.scheduleLookupPoll();
            return;
        }
        if (!this.live) return;
        for (const missing of waiting) {
            const result = data.results?.[missing.lookup_key];
            if (!result || result.state === "pending") continue;
            missing.lookup_state = result.state === "found" ? "found" : "not_found";
            missing.download_source = result.source || null;
            this.refresh(missing);
        }
        if ([...this.missing.values()].some((m) => m.lookup_state === "pending")) this.scheduleLookupPoll();
    }

    async searchAgain(missing) {
        try {
            const { original_path, category, source_url, source_hash, source_hash_type } = missing;
            const result = await this.request("/model_linker/lookups/refresh", {
                missing: { original_path, category, source_url, source_hash, source_hash_type },
            });
            for (const other of this.missing.values()) {
                if (other.lookup_key === missing.lookup_key) {
                    other.lookup_key = result.key;
                    other.lookup_state = "pending";
                    this.refresh(other);
                }
            }
            this.scheduleLookupPoll();
        } catch (error) {
            notifyError("Could not start the search", error);
        }
    }

    async downloadAll() {
        const seen = new Set();
        let started = 0;
        for (const missing of this.missing.values()) {
            const record = this.records[this.byRef.get(refSlot(missing))];
            if (downloadState(missing, record).kind !== "available") continue;
            if (seen.has(fileKey(missing))) continue;
            seen.add(fileKey(missing));
            await this.start(missing);
            started++;
        }
        if (!started) {
            app.extensionManager?.toast?.add({ severity: "info", summary: "Model Linker",
                detail: "Nothing to download yet - lookups may still be running.", life: 4000 });
        }
    }

    stop() {
        this.live = false;
        clearTimeout(this.progressTimer);
        clearTimeout(this.lookupTimer);
        this.progressTimer = this.lookupTimer = null;
    }
}
