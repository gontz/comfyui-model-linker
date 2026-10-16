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
import { escapeHtml, notifyError, presentModelGroupKey, safeHttpUrl } from "./util.js";

const OPTIONS = [
    ["note", "Add a \"Model links\" note"],
    ["properties", "Write links into the loader nodes"],
    ["node", "Add a Model Linker node (muted)"],
];
const basename = (path) => (path || "").split(/[\\/]/).pop();
const SHA256 = /^[0-9a-f]{64}$/i;
/** /export/lookup takes at most this many models per request (routes_export.MAX_MODELS). */
export const LOOKUP_BATCH = 500;

/**
 * Every node of `graph` and of every subgraph definition reachable from it -
 * through an instance's `subgraph`, or the root's `subgraphs` map, which also
 * holds definitions with no instance on the canvas.
 */
export function allGraphNodes(graph) {
    const nodes = new Set();
    const seen = new Set();
    const visit = (g) => {
        if (!g || seen.has(g)) return;
        seen.add(g);
        for (const node of g._nodes || g.nodes || []) {
            nodes.add(node);
            let inner = null;
            try { inner = node?.subgraph; } catch (e) { /* not a subgraph instance */ }
            visit(inner);
        }
        for (const sub of g.subgraphs?.values?.() ?? []) visit(sub);
    };
    visit(graph);
    return nodes;
}

/** One row per (folder, file) across missing and present models. Pure. */
export function exportRows(data) {
    const rows = new Map();
    const add = (ref, present) => {
        const category = ref.canonical_category || ref.category || "unknown";
        const key = presentModelGroupKey(ref);   // "sub\a" and "sub/a" are one file
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
        // The browser's own reading of the link, which is what gets written:
        // `https:\\user:pw@host\a` is https://user:pw@host/a to a browser, but
        // Python's urlparse finds no host in it and would leave the userinfo
        existing_url: safeHttpUrl(row.url) || undefined,
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

    /**
     * /export/lookup in batches the server accepts. `links[i]` answers
     * `models[i]` across batches; a short answer counts as no link.
     */
    async lookupLinks(models, extra = {}) {
        const links = [];
        let searched = false;
        for (let start = 0; start < models.length; start += LOOKUP_BATCH) {
            const chunk = models.slice(start, start + LOOKUP_BATCH);
            const answer = await this.request("/model_linker/export/lookup", { ...extra, models: chunk });
            chunk.forEach((_, j) => links.push(answer?.links?.[j] ?? null));
            searched = answer?.searched === true;
        }
        return { links, searched };
    }

    show(data) {
        this.rows = exportRows(data);
        this.render();
        this.element.style.display = "flex";
        this.lookUp();
    }

    close() {
        this.element.style.display = "none";
        // Nothing still running - apply(), findByHash() - may still believe
        // it is working on this dialog's rows once it is closed
        this.rows = [];
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
            const { links, searched } = await this.lookupLinks(lookupPayload(rows), { manual });
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
        if (this._hashBusy?.has(i)) return;   // ignore a double press on the same row
        const row = this.rows[i];
        (this._hashBusy ??= new Set()).add(i);
        try {
            const { job_id } = await this.request("/model_linker/export/hash", { category: row.category, filename: row.original_path });
            if (this.rows[i] !== row) return;   // the dialog moved on while this was in flight
            for (;;) {
                await new Promise((resolve) => setTimeout(resolve, 1000));
                if (this.rows[i] !== row) return;
                const job = await this.request(`/model_linker/export/hash/${encodeURIComponent(job_id)}`);
                if (this.rows[i] !== row) return;
                if (job.status === "hashing") { this.setRow(i, "", `Hashing ${Math.round((job.progress || 0) * 100)}%`); continue; }
                if (job.status === "looking_up") { this.setRow(i, "", "Looking up…"); continue; }
                const url = safeHttpUrl(job.result?.url);
                this.setRow(i, url, job.status === "error" ? `✗ ${job.error}` : url ? "✓ Found by hash" : "No match for this file");
                return;
            }
        } catch (error) {
            notifyError("Hash lookup failed", error);
        } finally {
            this._hashBusy?.delete(i);
        }
    }

    /**
     * The existing `properties.models` entry a reference's widget value
     * resolves to, matched the same way the analyzer builds its own lookup
     * (`core/workflow_analyzer.py` - exact value first, falling back to
     * basename on both sides): otherwise the scrub below would miss an entry
     * the analyzer itself attributed to this reference, leaving a credential
     * in place and letting a brand-new entry get pushed alongside it.
     *
     * `claimed` is the set of indices another ref on this same node already
     * claims by its OWN exact name match - skipped by the basename fallback,
     * so two widgets sharing a basename (DualCLIPLoader's two
     * `t5.safetensors` under different subfolders) with an exact entry for
     * only one of them can never have the other's change land on it.
     */
    static findModelEntry(models, name, claimed) {
        let at = models.findIndex((m) => m?.name === name);
        if (at < 0) {
            const base = basename(name);
            at = models.findIndex((m, i) => basename(m?.name) === base && !claimed?.has(i));
        }
        return at;
    }

    /**
     * Whatever a browser reads as http(s) - `https:\\host\a` and `https:host/a`
     * included. Such a link goes to strip_secrets in that reading
     * (safeHttpUrl's href), never as the raw string.
     */
    static isHttpUrl(value) {
        return !!safeHttpUrl(value);
    }

    /**
     * A non-http(s) URL (`s3://...`, etc.) that `strip_secrets` never
     * touches server-side (it is a no-op there for any scheme but http(s)):
     * kept only if it carries no credential-shaped part of its own - no
     * userinfo, query, fragment, or `;` anywhere (a non-special scheme's
     * host is opaque, so `s3://bucket;token=X/a` hides one outside the path).
     */
    static cleanNonHttpUrl(value) {
        if (typeof value !== "string" || value.includes(";")) return false;
        try {
            const parsed = new URL(value);
            return !parsed.username && !parsed.password && !parsed.search && !parsed.hash;
        } catch (e) {
            return false;
        }
    }

    /**
     * Resolve every row's link through the server (found, pasted or the
     * workflow's own, http or https - strip_secrets cleans any of them), then
     * write the result into the workflow: a "Model links" note, the loader
     * nodes' own `properties.models`, and a muted marker node.
     *
     * Three kinds of `properties.models` entry, three different rules:
     * - MATCHED (a ref resolves to it): with "write links into the loader
     *   nodes" on, it is updated (or cleared) to the row's resolved link,
     *   same as adding one. Off, it is only ever scrubbed of its OWN
     *   credential - the row's resolved link replaces it only when that
     *   turns out to be the very same address (nothing to replace, only to
     *   clean); a genuinely different one (user-pasted, hash-found, or found
     *   online) is never substituted.
     * - A BRAND-NEW entry (no ref matched anything already there) is only
     *   ever added when the option is on, and only from an https link - the
     *   one scheme ever added fresh. Same restriction for the note.
     * - ORPHAN (no ref claims it at all - including every entry on a node no
     *   ref resolves to, anywhere in the graph or its subgraph definitions):
     *   the option has no say over these either way - they are only ever
     *   scrubbed, never added or replaced.
     *
     * Only the `url` key is scrubbed; any other field an entry carries is
     * left as the workflow had it.
     */
    async apply() {
        if (this._applying) return;   // ignore a double press
        this._applying = true;
        try {
            const rows = this.rows;   // snapshot: bail if the dialog moves on under us
            const graph = app.graph;
            const LG = globalThis.LiteGraph;
            if (!graph || !LG) { notifyError("Export", new Error("Canvas not available")); return; }

            const writeLinksOn = this.option("properties");

            // Every node a reference resolves to, and every ref (from any
            // row) that resolves there too - the basename fallback above
            // needs the full set per node, not just the one ref being
            // matched right now.
            const nodeRefs = new Map();
            for (const row of rows) {
                for (const ref of row.refs) {
                    for (const node of this.linker.liveNodesFor(ref)) {
                        if (!nodeRefs.has(node)) nodeRefs.set(node, []);
                        nodeRefs.get(node).push(ref);
                    }
                }
            }
            const claimedByNode = new Map();
            const matchFor = (node, models, name) => {
                if (!claimedByNode.has(node)) {
                    const claimed = new Set();
                    for (const ref of nodeRefs.get(node) || []) {
                        const exact = models.findIndex((m) => m?.name === ref.original_path);
                        if (exact >= 0) claimed.add(exact);
                    }
                    claimedByNode.set(node, claimed);
                }
                return ExportDialog.findModelEntry(models, name, claimedByNode.get(node));
            };

            // Which entry (if any) every (node, ref) pair matches, decided
            // purely by name - safe to work out before any network
            // round-trip or mutation. Every OTHER entry on a node a ref
            // resolves to, that no ref claims at all, is an orphan.
            const matchByRow = new Map();      // row -> [{node, ref, at}]
            const touchedByNode = new Map();   // node -> Set<index>
            for (const row of rows) {
                const matches = [];
                for (const ref of row.refs) {
                    for (const node of this.linker.liveNodesFor(ref)) {
                        const models = Array.isArray(node.properties?.models) ? node.properties.models : [];
                        const at = matchFor(node, models, ref.original_path);
                        // The entry's name, to find it again after the awaits below
                        matches.push({ node, ref, at, name: at >= 0 ? models[at]?.name : undefined });
                        if (at >= 0) {
                            if (!touchedByNode.has(node)) touchedByNode.set(node, new Set());
                            touchedByNode.get(node).add(at);
                        }
                    }
                }
                matchByRow.set(row, matches);
            }
            // Every node in the workflow, not only those a reference resolves
            // to: a widget the analyzer does not recognise, or a subgraph
            // definition with no instance, still ships its properties.models.
            // On a node no row touched, every entry is an orphan.
            const orphans = [];   // { node, index, name, url }
            for (const node of new Set([...allGraphNodes(graph), ...nodeRefs.keys()])) {
                const touched = touchedByNode.get(node) ?? new Set();
                const models = Array.isArray(node?.properties?.models) ? node.properties.models : [];
                models.forEach((entry, index) => {
                    if (touched.has(index) || !entry || typeof entry !== "object") return;
                    // Any url but an empty one: a non-string (array, object)
                    // cannot be checked, so it resolves to "" and is deleted
                    if (entry.url !== undefined && entry.url !== "") orphans.push({ node, index, name: entry.name, url: entry.url });
                });
            }

            const candidates = rows.filter((row) => safeHttpUrl(row.url));
            const candidateRows = new Set(candidates);

            // A matched entry's OWN current link, needed only when "write
            // links" is off: the one case where what gets written is a
            // choice between the row's resolved link and the entry's own,
            // rather than simply the row's. A row with no usable link of its
            // own resolves to "" regardless (see resolvedUrl below), so
            // there is nothing to compare against and nothing to collect.
            const selfCandidates = [];   // { match, url }
            if (!writeLinksOn) {
                for (const [row, matches] of matchByRow) {
                    if (!candidateRows.has(row)) continue;
                    for (const match of matches) {
                        if (match.at < 0) continue;
                        const models = Array.isArray(match.node.properties?.models) ? match.node.properties.models : [];
                        const url = models[match.at]?.url;
                        if (typeof url === "string" && url) selfCandidates.push({ match, url });
                    }
                }
            }

            const httpOrphans = orphans.filter((o) => ExportDialog.isHttpUrl(o.url));
            const httpSelf = selfCandidates.filter((s) => ExportDialog.isHttpUrl(s.url));

            const resolved = new Map();          // row.key -> stripped candidate url (any scheme) or ""
            const orphanResolved = new Map();    // orphan -> stripped url or ""
            const selfResolved = new Map();      // match -> stripped url or ""

            if (candidates.length || httpOrphans.length || httpSelf.length) {
                // Every http(s) link needing a strip - the row candidates,
                // plus an orphan's or a matched entry's own current link -
                // goes through the server's strip_secrets in this one batch.
                // Never reimplemented here.
                const scrubItem = (url) => {
                    const href = safeHttpUrl(url);
                    return { filename: basename(href) || "model", existing_url: href };
                };
                const payload = [
                    ...lookupPayload(candidates),
                    ...httpOrphans.map((o) => scrubItem(o.url)),
                    ...httpSelf.map((s) => scrubItem(s.url)),
                ];
                let links;
                try {
                    ({ links } = await this.lookupLinks(payload));
                } catch (error) { notifyError("Could not prepare the links", error); return; }
                if (rows !== this.rows) return;
                let i = 0;
                candidates.forEach((row) => resolved.set(row.key, safeHttpUrl(links?.[i++]?.url) || ""));
                httpOrphans.forEach((o) => orphanResolved.set(o, safeHttpUrl(links?.[i++]?.url) || ""));
                httpSelf.forEach((s) => selfResolved.set(s.match, safeHttpUrl(links?.[i++]?.url) || ""));
            }
            // Non-http(s) (s3://, etc.): strip_secrets itself is a no-op for
            // any scheme but http(s), so a round-trip would tell us nothing.
            // A bare, credential-free address is kept as is; one carrying
            // userinfo, a query or a fragment is dropped outright.
            orphans.filter((o) => !ExportDialog.isHttpUrl(o.url))
                .forEach((o) => orphanResolved.set(o, ExportDialog.cleanNonHttpUrl(o.url) ? o.url : ""));
            selfCandidates.filter((s) => !ExportDialog.isHttpUrl(s.url))
                .forEach((s) => selfResolved.set(s.match, ExportDialog.cleanNonHttpUrl(s.url) ? s.url : ""));

            // Whatever the server (or the non-http check above) sent back
            // for this row - any scheme, or "" when there was nothing to
            // resolve.
            const resolvedUrl = (row) => resolved.get(row.key) || "";
            // The https-only view of the same answer: what a brand-new
            // entry, or the note, may ever carry.
            const finalUrl = (row) => {
                const url = resolvedUrl(row);
                return url.startsWith("https://") ? url : "";
            };
            const linked = rows.filter((row) => finalUrl(row)).map((row) => ({ ...row, url: finalUrl(row) }));

            // The note is optional: when it cannot be built (the server
            // refuses more than 500 models, say), the node links - scrubs
            // included - are still written, and the note is left as it was
            let markdown = "";
            let noteFailed = false;
            if (this.option("note") && linked.length) {
                try {
                    ({ markdown } = await this.request("/model_linker/export/markdown", {
                        models: linked.map((row) => ({ name: row.name, url: row.url, directory: row.category })),
                    }));
                } catch (error) {
                    noteFailed = true;
                    markdown = "";
                    notifyError("Could not build the links note; the node links are written without it", error);
                }
                if (rows !== this.rows) return;
            }

            app.canvas?.emitBeforeChange?.();
            try {
                const nodes = graph._nodes || graph.nodes || [];
                const minX = Math.min(...nodes.map((n) => n.pos?.[0] ?? 0), 0);
                const minY = Math.min(...nodes.map((n) => n.pos?.[1] ?? 0), 0);
                const existingNote = nodes.find((n) => n.properties?.model_linker_note);
                let note = existingNote || null;
                // Touching the note at all - creating, updating or clearing
                // it - is exactly what the "note" option governs; off means
                // leave whatever is already there alone.
                if (this.option("note")) {
                    if (markdown) {
                        note = existingNote || LG.createNode("MarkdownNote") || LG.createNode("Note");
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
                    } else if (existingNote && !noteFailed) {
                        // The option is on but nothing is linked any more:
                        // leave no stale note (or stale credential inside one)
                        if (existingNote.widgets?.[0]) existingNote.widgets[0].value = "";
                        existingNote.widgets_values = [""];
                    }
                }

                // Matched entries: see the three-way rule in the doc comment
                // above. Adding a brand-new entry is the only part "write
                // links into the loader nodes" gates for these.
                // The graph may have changed during the awaits above: find
                // each entry again by what it was, never by a stored index alone
                const refind = (models, index, isIt) => (isIt(models[index]) ? index : models.findIndex(isIt));
                for (const row of rows) {
                    const addUrl = finalUrl(row);
                    const rowResolved = resolvedUrl(row);
                    for (const match of matchByRow.get(row) || []) {
                        const { node, ref } = match;
                        const models = Array.isArray(node.properties?.models) ? node.properties.models : [];
                        const at = match.at < 0 ? -1 : refind(models, match.at, (m) => m?.name === match.name);
                        if (match.at >= 0 && at < 0) continue;   // gone meanwhile: nothing to scrub, nothing to add
                        if (at >= 0) {
                            let writeUrl;
                            if (!rowResolved) {
                                // Nothing resolved for this row (cleared, or
                                // never had a candidate - a non-http entry's
                                // own link is never sent as one). An http
                                // entry with nothing to replace it is
                                // cleared; a clean non-http one survives.
                                const current = models[at]?.url;
                                writeUrl = (typeof current === "string" && current && !ExportDialog.isHttpUrl(current)
                                    && ExportDialog.cleanNonHttpUrl(current)) ? current : "";
                            } else if (writeLinksOn) {
                                writeUrl = rowResolved;
                            } else {
                                const selfUrl = selfResolved.get(match) || "";
                                writeUrl = rowResolved === selfUrl ? rowResolved : selfUrl;
                            }
                            const { url: _stale, ...rest } = models[at];
                            models[at] = writeUrl ? { ...rest, url: writeUrl } : rest;
                            node.properties = { ...(node.properties || {}), models };
                        } else if (writeLinksOn && addUrl) {
                            models.push({ name: ref.original_path, url: addUrl, directory: row.category });
                            node.properties = { ...(node.properties || {}), models };
                        }
                    }
                }

                // Orphans: no ref claims these at all, so "write links" has
                // nothing to say about them - only ever scrubbed.
                for (const orphan of orphans) {
                    const { node, name, url } = orphan;
                    const models = Array.isArray(node.properties?.models) ? node.properties.models : [];
                    const index = refind(models, orphan.index, (m) => m?.name === name && m?.url === url);
                    if (index < 0) continue;
                    const nextUrl = orphanResolved.get(orphan) || "";
                    if (nextUrl === url) continue;   // unchanged - nothing to write
                    const { url: _stale, ...rest } = models[index];
                    models[index] = nextUrl ? { ...rest, url: nextUrl } : rest;
                    node.properties = { ...(node.properties || {}), models };
                }

                if (this.option("node")) {
                    const existingMarker = nodes.find((n) => n.type === "ModelLinker");
                    if (existingMarker) {
                        // Re-mute on every re-apply: nothing should be able to
                        // leave this live behind the user's back
                        existingMarker.mode = 2;
                    } else {
                        const marker = LG.createNode("ModelLinker");
                        if (marker) {
                            // Muted (LiteGraph NEVER): graphToPrompt leaves it out.
                            // ComfyUI rejects a whole prompt naming a node type it
                            // does not have, so an unmuted marker would stop the
                            // shared workflow running anywhere Model Linker isn't.
                            marker.mode = 2;
                            marker.pos = note ? [note.pos[0], note.pos[1] - 120] : [minX - 480, minY - 120];
                            graph.add(marker);
                        }
                    }
                }
            } finally {
                app.canvas?.emitAfterChange?.();
                graph.setDirtyCanvas?.(true, true);
            }
            app.extensionManager?.toast?.add({ severity: "success", summary: "Model Linker",
                detail: `Export applied (${linked.length} link${linked.length === 1 ? "" : "s"}). Save the workflow to keep it.`, life: 5000 });
            this.close();
        } finally {
            this._applying = false;
        }
    }
}
