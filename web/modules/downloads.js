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
const categoryKey = (category) => (category || "unknown").toLowerCase();

/**
 * The category a missing model's download is actually filed under - the
 * same choice start() makes when it posts to /model_linker/download. When
 * the card's own category is "unknown" (a popular/model-list match named
 * the folder, not the workflow), that is the download source's own
 * directory, not "unknown": fileKey must mirror this exactly, or a card
 * whose category was never known cannot find its own download on a fresh
 * controller (e.g. after a page reload) - it only has the source to go on.
 */
const effectiveCategory = (m) => {
    if (m.category && m.category !== "unknown") return m.canonical_category || m.category;
    const source = m.download_source || {};
    return source.canonical_category || source.directory;
};

/**
 * The identity a download is tracked and deduped by: canonical category plus
 * filename. Two categories that are really the same folder (`clip` and
 * `text_encoders`) must collapse to one key, or an alias sends a second
 * download of the same file - and the server's own "already downloading"
 * refusal would surface to the user as an error for doing nothing wrong.
 */
const fileKey = (m) => `${categoryKey(effectiveCategory(m))}|${basename(m.original_path).toLowerCase()}`;

/** The same identity, computed from a /progress record instead of a missing model. */
const recordKey = (r) => `${categoryKey(r.canonical_category || r.category)}|${basename(r.filename).toLowerCase()}`;

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
    if (missing?.lookup_state === "idle") return { kind: "idle" };
    // lookup_state is undefined: the lookups subsystem never ran for this card
    // (e.g. `requests` is not installed, so routes_downloads never registered).
    // A manual "Find a download" button would just 404 - show nothing instead.
    return { kind: "none" };
}

export class DownloadController {
    constructor(dialog) {
        this.dialog = dialog;
        this.container = null;
        // Rebuilt by afterRender() on every render that has missing models,
        // and wiped by reset() on the branches that don't (zero missing, no
        // workflow, an analysis error) - see reset()'s own comment.
        this.missing = new Map();   // refSlot -> missing model
        // Keyed by file identity (fileKey), not by card: a download belongs
        // to a file, not to whichever card happened to start it, and several
        // cards can reference the same file. Server state, so it survives
        // both reset() and a fresh afterRender().
        this.byFile = new Map();    // fileKey -> download id
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
        const record = this.records[this.byFile.get(fileKey(missing))];
        const state = downloadState(missing, record);
        // dataset.mlSlot hands back the unescaped slot, which is the map key.
        // It identifies which card was clicked - not which file: cancel()
        // and start() both resolve the file (and its shared download) from
        // the card via fileKey().
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

    /** Re-render every currently-rendered card referencing the same file. */
    refreshAllFor(key) {
        for (const missing of this.missing.values()) {
            if (fileKey(missing) === key) this.refresh(missing);
        }
    }

    /**
     * Drop everything derived from the last render. Called once, at the top
     * of the dialog's loadWorkflowData() - before analysis even runs - so it
     * covers every branch that does not end in afterRender(): zero missing
     * models, no workflow loaded, and an analysis error. Without this, a
     * card from a previous workflow stays in `missing` forever, and a new
     * card that happens to land on the same node/widget slot would be
     * confused for the old one by anything still keyed on refSlot.
     *
     * Download records in `byFile`/`records` are not reset - those describe
     * downloads actually running on the server, which outlive any one
     * analysis and must keep being polled/shown when their file reappears.
     */
    reset() {
        this.missing = new Map();
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
        const key = fileKey(missing);
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
            // Every card whose fileKey matches picks this up on its own the
            // next time it renders (blockHtml looks the download up by file,
            // not by card) - track() just makes that visible right away.
            this.track(key, data.download_id);
            this.scheduleProgressPoll();
        } catch (error) {
            notifyError("Download did not start", error);
        }
    }

    track(key, downloadId, record = { status: "queued", progress: 0 }) {
        this.byFile.set(key, downloadId);
        this.records[downloadId] = this.records[downloadId] || record;
        this.refreshAllFor(key);
    }

    async cancel(missing) {
        const id = this.byFile.get(fileKey(missing));
        if (!id) return;
        try {
            const data = await this.request(`/model_linker/cancel/${encodeURIComponent(id)}`, {});
            if (!data?.success) notifyError("Could not cancel the download", new Error("The server could not cancel it"));
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
        for (const [key, id] of [...this.byFile]) {
            const record = all[id];
            if (!record) {
                // Gone from the server - e.g. a restart lost its progress
                // state. Stop treating it as running rather than leaving the
                // card frozen mid-progress and polling forever.
                this.byFile.delete(key);
                delete this.records[id];
                this.refreshAllFor(key);
                continue;
            }
            const before = this.records[id];
            this.records[id] = record;
            this.refreshAllFor(key);
            if (downloadState(null, record).kind === "active") active = true;
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
        // Only a record whose file key matches something actually rendered
        // right now - an unrelated download (another tab, an earlier
        // workflow, someone else's model) must not be adopted just because
        // it happens to still be running on the server: adopting it would
        // keep polling alive for no card on screen, and a false "Downloaded"
        // toast plus a full re-analysis once it finishes.
        const onScreen = new Set([...this.missing.values()].map(fileKey));
        let any = false;
        for (const [id, record] of Object.entries(all)) {
            if (downloadState(null, record).kind !== "active") continue;
            const key = recordKey(record);
            if (!onScreen.has(key)) continue;
            if (this.byFile.get(key) === id) continue; // already tracked
            this.track(key, id, record);
            any = true;
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
        let alreadyActive = false;
        for (const missing of this.missing.values()) {
            const key = fileKey(missing);
            const record = this.records[this.byFile.get(key)];
            const kind = downloadState(missing, record).kind;
            if (kind === "active") { alreadyActive = true; continue; }
            if (kind !== "available" || seen.has(key)) continue;
            seen.add(key);
            await this.start(missing);
            started++;
        }
        // Only "nothing to download" when there is genuinely nothing - not
        // when everything found is already downloading from the last click.
        if (!started && !alreadyActive) {
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
