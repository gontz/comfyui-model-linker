/**
 * Export: prepare a workflow for sharing. Finds a public download link for
 * every model, then (each optional) adds a "Model links" note like the
 * official templates, writes the links into the loader nodes' properties.models
 * so ComfyUI's own missing-models downloader can use them, and adds a muted
 * Model Linker node. Every link written - found, pasted or the workflow's own -
 * comes back from the server with credentials stripped.
 */

import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

import { $el } from "./dom.js";
import { escapeHtml, notifyError, safeHttpUrl } from "./util.js";

const OPTIONS = [
    ["note", "Add a \"Model links\" note"],
    ["properties", "Write links into the loader nodes"],
    ["node", "Add a Model Linker node (muted)"],
];
const basename = (path) => (path || "").split(/[\\/]/).pop();
const SHA256 = /^[0-9a-f]{64}$/i;

/** One row per (folder, file) across missing and present models. Pure. */
export function exportRows(data) {
    const rows = new Map();
    const add = (ref, present) => {
        const category = ref.canonical_category || ref.category || "unknown";
        const key = `${category}|${ref.original_path}`;
        if (!rows.has(key)) {
            rows.set(key, { key, name: basename(ref.original_path), category, original_path: ref.original_path,
                url: "", sha256: "", present, refs: [] });
        }
        const row = rows.get(key);
        row.refs.push(ref);
        row.present = row.present || present;
        if (!row.url) row.url = safeHttpUrl(ref.source_url) || "";
        const hashType = (ref.source_hash_type || "sha256").toLowerCase();
        if (!row.sha256 && hashType === "sha256" && SHA256.test(ref.source_hash || "")) row.sha256 = ref.source_hash;
    };
    for (const ref of data?.missing_models || []) add(ref, false);
    for (const ref of data?.present_models || []) add(ref, true);
    return [...rows.values()].sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name));
}

/**
 * What /export/lookup gets: every row, its link included. A row with a link is
 * never searched - the server only strips credentials from it. Pure.
 */
export function lookupPayload(rows) {
    return rows.map((row) => ({
        filename: row.name,
        category: row.category,
        sha256: row.sha256 || undefined,
        existing_url: row.url || undefined,
    }));
}

export class ExportDialog {
    constructor(linker) {
        this.linker = linker;   // the LinkerManagerDialog, for liveNodesFor()
        this.rows = [];
        this.element = $el("div.comfy-modal", {
            id: "model-linker-export-modal",
            parent: document.body,
            style: {
                position: "fixed", top: "50%", left: "50%", transform: "translate(-50%, -50%)",
                width: "760px", maxWidth: "95vw", maxHeight: "90vh", display: "none", flexDirection: "column",
                backgroundColor: "var(--comfy-menu-bg, #202020)", color: "var(--input-text, #fff)",
                border: "2px solid var(--border-color, #555)", borderRadius: "8px", zIndex: "100000",
            },
        });
    }

    async request(path, body) {
        const response = await api.fetchApi(path, body === undefined ? {} : {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data?.error || `HTTP ${response.status}`);
        return data;
    }

    show(data) {
        this.rows = exportRows(data);
        this.render();
        this.element.style.display = "flex";
        this.lookUp();
    }

    close() {
        this.element.style.display = "none";
    }

    option(id) {
        try { return localStorage.getItem(`modelLinker.export.${id}`) !== "0"; } catch (e) { return true; }
    }

    render() {
        const rowsHtml = this.rows.map((row, i) => `
            <tr>
              <td style="padding:4px;"><code title="${escapeHtml(row.original_path)}">${escapeHtml(row.name)}</code>
                <div style="opacity:.6; font-size:11px;">${escapeHtml(row.category)} · ${row.refs.length} node(s)</div></td>
              <td style="padding:4px; width:55%;">
                <input data-row="${i}" type="url" value="${escapeHtml(row.url || "")}" placeholder="https://… (paste or find)" style="width:100%;" />
                <div id="ml-export-status-${i}" style="opacity:.6; font-size:11px;"></div></td>
              <td style="padding:4px; white-space:nowrap;">
                ${row.present ? `<button data-hash="${i}" class="model-linker-resolve-btn" style="padding:2px 6px;" title="Identify this file by its SHA-256">Find by hash</button>` : ""}
              </td>
            </tr>`).join("");
        this.element.innerHTML = `
            <div style="display:flex; justify-content:space-between; padding:12px 16px; border-bottom:1px solid var(--border-color);">
              <strong>Export for sharing</strong><button data-close style="background:none; border:none; color:inherit; font-size:20px; cursor:pointer;">×</button>
            </div>
            <div style="overflow:auto; padding:8px 16px; flex:1;">
              <table style="width:100%; border-collapse:collapse;">${rowsHtml || "<tr><td>No models in this workflow.</td></tr>"}</table>
            </div>
            <div style="display:flex; gap:16px; align-items:center; padding:12px 16px; border-top:1px solid var(--border-color); flex-wrap:wrap;">
              ${OPTIONS.map(([id, label]) => `<label><input type="checkbox" data-option="${id}" ${this.option(id) ? "checked" : ""}/> ${escapeHtml(label)}</label>`).join("")}
              <button data-find class="model-linker-resolve-btn" style="display:none; padding:4px 12px;" title="Background lookups are off in ⚙ Settings. This searches HuggingFace and CivitAI once, for the rows without a link.">🔍 Find links</button>
              <button data-apply class="model-linker-resolve-btn" style="margin-left:auto; padding:4px 12px;">Apply to workflow</button>
            </div>`;
        this.element.querySelector("[data-close]").onclick = () => this.close();
        this.element.querySelector("[data-apply]").onclick = () => this.apply();
        this.element.querySelector("[data-find]").onclick = () => this.lookUp({ manual: true });
        this.element.querySelectorAll("input[data-row]").forEach((input) => {
            input.onchange = () => { this.rows[Number(input.dataset.row)].url = input.value.trim(); };
        });
        this.element.querySelectorAll("input[data-option]").forEach((box) => {
            box.onchange = () => { try { localStorage.setItem(`modelLinker.export.${box.dataset.option}`, box.checked ? "1" : "0"); } catch (e) { } };
        });
        this.element.querySelectorAll("button[data-hash]").forEach((button) => {
            button.onclick = () => this.findByHash(Number(button.dataset.hash));
        });
    }

    setRow(i, url, status) {
        const row = this.rows[i];
        if (url) row.url = url;
        const input = this.element.querySelector(`input[data-row="${i}"]`);
        if (input && url) input.value = url;
        const label = this.element.querySelector(`#ml-export-status-${i}`);
        if (label) label.textContent = status;
    }

    /**
     * Sends every row: links already present come back stripped of credentials,
     * the rest are searched for - unless background lookups are off and this is
     * not a press of "Find links", in which case the server sends nothing out.
     */
    async lookUp({ manual = false } = {}) {
        const rows = this.rows;
        if (!rows.length) return;
        rows.forEach((row, i) => { if (!row.url) this.setRow(i, "", "Looking for a public link…"); });
        try {
            const { links, searched } = await this.request("/model_linker/export/lookup",
                { models: lookupPayload(rows), manual });
            if (rows !== this.rows) return;   // the dialog was reopened meanwhile
            rows.forEach((row, i) => {
                const link = links?.[i];
                const url = safeHttpUrl(link?.url);
                if (url) {
                    this.setRow(i, url, link.url_source === "workflow" ? "From the workflow" : `Found via ${link.url_source || link.source}`);
                } else if (!row.url) {
                    this.setRow(i, "", searched ? "No public link found" : "Online lookups are off");
                }
            });
            this.element.querySelector("[data-find]").style.display = searched ? "none" : "";
        } catch (error) {
            rows.forEach((row, i) => { if (!row.url) this.setRow(i, "", "Lookup failed"); });
            notifyError("Link lookup failed", error);
        }
    }

    async findByHash(i) {
        const row = this.rows[i];
        try {
            const { job_id } = await this.request("/model_linker/export/hash", { category: row.category, filename: row.original_path });
            for (;;) {
                await new Promise((resolve) => setTimeout(resolve, 1000));
                const job = await this.request(`/model_linker/export/hash/${encodeURIComponent(job_id)}`);
                if (job.status === "hashing") { this.setRow(i, "", `Hashing ${Math.round((job.progress || 0) * 100)}%`); continue; }
                if (job.status === "looking_up") { this.setRow(i, "", "Looking up…"); continue; }
                const url = safeHttpUrl(job.result?.url);
                this.setRow(i, url, job.status === "error" ? `✗ ${job.error}` : url ? "✓ Found by hash" : "No match for this file");
                return;
            }
        } catch (error) {
            notifyError("Hash lookup failed", error);
        }
    }

    async apply() {
        const graph = app.graph;
        const LG = globalThis.LiteGraph;
        if (!graph || !LG) { notifyError("Export", new Error("Canvas not available")); return; }

        // Every link written into the workflow - found, pasted, or the
        // workflow's own - goes through the server's strip_secrets first. These
        // rows all have a link, so nothing is searched and nothing leaves the machine.
        const candidates = this.rows.filter((row) => safeHttpUrl(row.url)?.startsWith("https://"));
        let linked = [];
        if (candidates.length) {
            try {
                const { links } = await this.request("/model_linker/export/lookup", { models: lookupPayload(candidates) });
                linked = candidates
                    .map((row, n) => ({ ...row, url: safeHttpUrl(links?.[n]?.url) || "" }))
                    .filter((row) => row.url.startsWith("https://"));
            } catch (error) { notifyError("Could not prepare the links", error); return; }
        }

        let markdown = "";
        if (this.option("note") && linked.length) {
            try {
                ({ markdown } = await this.request("/model_linker/export/markdown", {
                    models: linked.map((row) => ({ name: row.name, url: row.url, directory: row.category })),
                }));
            } catch (error) { notifyError("Could not build the links note", error); return; }
        }

        app.canvas?.emitBeforeChange?.();
        try {
            const nodes = graph._nodes || graph.nodes || [];
            const minX = Math.min(...nodes.map((n) => n.pos?.[0] ?? 0), 0);
            const minY = Math.min(...nodes.map((n) => n.pos?.[1] ?? 0), 0);
            let note = null;
            if (markdown) {
                note = nodes.find((n) => n.properties?.model_linker_note)
                    || LG.createNode("MarkdownNote") || LG.createNode("Note");
                if (!note.graph) {
                    note.pos = [minX - 480, minY];
                    note.size = [440, 560];
                    note.title = "Model links";
                    note.color = "#222";
                    note.bgcolor = "#000";
                    note.properties = { ...(note.properties || {}), model_linker_note: true };
                    graph.add(note);
                }
                if (note.widgets?.[0]) note.widgets[0].value = markdown;
                note.widgets_values = [markdown];
            }
            if (this.option("properties")) {
                for (const row of linked) {
                    for (const ref of row.refs) {
                        for (const node of this.linker.liveNodesFor(ref)) {
                            const models = Array.isArray(node.properties?.models) ? node.properties.models : [];
                            const entry = { name: ref.original_path, url: row.url, directory: row.category };
                            const at = models.findIndex((m) => m?.name === entry.name);
                            if (at >= 0) models[at] = { ...models[at], ...entry }; else models.push(entry);
                            node.properties = { ...(node.properties || {}), models };
                        }
                    }
                }
            }
            if (this.option("node") && !nodes.find((n) => n.type === "ModelLinker")) {
                const marker = LG.createNode("ModelLinker");
                if (marker) {
                    // Muted (LiteGraph NEVER): graphToPrompt leaves it out. ComfyUI
                    // rejects a whole prompt naming a node type it does not have,
                    // so an unmuted marker would stop the shared workflow running
                    // anywhere Model Linker is not installed.
                    marker.mode = 2;
                    marker.pos = note ? [note.pos[0], note.pos[1] - 120] : [minX - 480, minY - 120];
                    graph.add(marker);
                }
            }
        } finally {
            app.canvas?.emitAfterChange?.();
            graph.setDirtyCanvas?.(true, true);
        }
        app.extensionManager?.toast?.add({ severity: "success", summary: "Model Linker",
            detail: `Export applied (${linked.length} link${linked.length === 1 ? "" : "s"}). Save the workflow to keep it.`, life: 5000 });
        this.close();
    }
}
