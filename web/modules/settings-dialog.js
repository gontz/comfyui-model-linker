/** Settings: API tokens (stored on the server) and background-lookup switch. */

import { api } from "../../../scripts/api.js";

import { $el } from "./dom.js";
import { notifyError } from "./util.js";

const PROVIDERS = [
    { id: "huggingface", key: "hf_token", label: "HuggingFace token",
      help: "A Read token from huggingface.co/settings/tokens. Needed for gated models." },
    { id: "civitai", key: "civitai_key", label: "CivitAI API key",
      help: "From civitai.com/user/account. Needed for login-only downloads." },
];

export class SettingsDialog {
    constructor() {
        this.rows = {};
        this.element = $el("div.comfy-modal", {
            id: "model-linker-settings-modal",
            parent: document.body,
            style: {
                position: "fixed", top: "50%", left: "50%", transform: "translate(-50%, -50%)",
                width: "560px", maxWidth: "95vw", maxHeight: "90vh", overflowY: "auto",
                backgroundColor: "var(--comfy-menu-bg, #202020)", color: "var(--input-text, #fff)",
                border: "2px solid var(--border-color, #555)", borderRadius: "8px",
                padding: "16px", zIndex: "100000", display: "none", flexDirection: "column", gap: "14px",
            },
        }, [
            $el("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } }, [
                $el("strong", { textContent: "Model Linker settings" }),
                $el("button", { textContent: "×", onclick: () => this.close(),
                    style: { background: "none", border: "none", color: "inherit", fontSize: "20px", cursor: "pointer" } }),
            ]),
            ...PROVIDERS.map((p) => this.providerRow(p)),
            this.lookupRow(),
            this.backfillRow(),
        ]);
    }

    providerRow(provider) {
        const input = $el("input", { type: "password", autocomplete: "off",
            style: { flex: "1", minWidth: "0" } });
        const status = $el("div", { style: { fontSize: "12px", opacity: "0.8" } });
        const button = (text, onclick) => $el("button.model-linker-resolve-btn",
            { textContent: text, onclick, style: { padding: "2px 10px" } });
        const save = button("Save", () => this.save({ [provider.key]: input.value }, provider.id));
        const checkBtn = button("Check", () => this.check(provider.id));
        const clear = button("Clear", () => this.save({ [provider.key]: "" }));
        this.rows[provider.id] = { input, status, checkBtn, clear };
        return $el("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } }, [
            $el("label", { textContent: provider.label, style: { fontWeight: "600" } }),
            $el("div", { style: { display: "flex", gap: "6px" } }, [input, save, checkBtn, clear]),
            $el("div", { textContent: provider.help, style: { fontSize: "12px", opacity: "0.6" } }),
            status,
        ]);
    }

    lookupRow() {
        this.autoLookup = $el("input", { type: "checkbox",
            onchange: () => this.save({ auto_lookup: this.autoLookup.checked }) });
        return $el("div", { style: { display: "flex", flexDirection: "column", gap: "4px",
                borderTop: "1px solid var(--border-color)", paddingTop: "12px" } }, [
            $el("label", { style: { display: "flex", gap: "8px", alignItems: "center", fontWeight: "600" } }, [
                this.autoLookup, document.createTextNode("Find downloads for missing models in the background"),
            ]),
            $el("div", { style: { fontSize: "12px", opacity: "0.6" },
                textContent: "After each analysis, missing model filenames (and hashes, when the workflow "
                    + "records them) are looked up on HuggingFace and CivitAI. Results are kept, so they "
                    + "appear even if you close this window. Turn this off to keep filenames on this machine." }),
        ]);
    }

    backfillRow() {
        this.useAi = $el("input", { type: "checkbox" });
        this.backfillStatus = $el("div", { style: { fontSize: "12px", opacity: "0.8", whiteSpace: "pre-line" } });
        this.backfillButton = $el("button.model-linker-resolve-btn", {
            textContent: "Fill empty model info from HuggingFace",
            style: { padding: "2px 10px", alignSelf: "flex-start" },
            onclick: () => this.startBackfill(),
        });
        return $el("div", { style: { display: "flex", flexDirection: "column", gap: "6px",
                borderTop: "1px solid var(--border-color)", paddingTop: "12px" } }, [
            $el("strong", { textContent: "Lora Manager" }),
            $el("div", { style: { fontSize: "12px", opacity: "0.6" },
                textContent: "For models whose Lora Manager info is empty: finds the HuggingFace repo each file "
                    + "came from (by its SHA-256, never by name alone - filenames are sent to HuggingFace's "
                    + "search to find candidates, which are then verified), links it, and has Lora Manager read "
                    + "the model card. A link to another site is left alone. Runs on the server; you can close "
                    + "this window. Models it cannot identify can be linked by hand in Lora Manager and are "
                    + "filled on the next run." }),
            $el("label", { style: { display: "flex", gap: "8px", alignItems: "center" } }, [
                this.useAi, document.createTextNode("Also use Lora Manager's AI enrichment - may replace trigger "
                    + "words, notes, usage tips and base model; applied only to models that currently have none "
                    + "of those"),
            ]),
            this.backfillButton,
            this.backfillStatus,
        ]);
    }

    renderBackfill(state) {
        const c = state?.counts || {};
        const order = [["hydrated", "hydrated"], ["no_card", "no card"], ["linked_elsewhere", "linked elsewhere"],
            ["not_found", "not found"], ["card_would_hang", "card would freeze Lora Manager"],
            ["card_unchecked", "card unchecked"], ["skipped", "skipped"], ["failed", "failed"]];
        // AI-skip count stays out of `order` and gets its own separator -
        // it's not an outcome of the fill itself, just a note about which
        // hydrated models the (opt-in) AI step was allowed to touch.
        const summary = order.filter(([key]) => c[key]).map(([key, label]) => `${label} ${c[key]}`).join(" · ")
            + (c.ai_skipped ? ` · AI skipped ${c.ai_skipped}` : "")
            + (state?.limit ? ` · sample of ${state.total || 0} from ${state.candidates_total || 0} empty models` : "")
            + (state?.ai_error ? `\nAI enrichment did not run: ${state.ai_error}` : "");
        // The counts above are an aggregate; when something stopped the run
        // (e.g. Lora Manager itself stopping responding - the top-level
        // `error` names which file was in progress, but not what happened
        // to the handful of models right before it), the last few actual
        // results make that visible without building a full results table.
        // Plain text via textContent, same as everything else here - safe
        // regardless of what `file`/`error` (workflow- and disk-derived)
        // actually contain.
        const recent = (state?.results || []).slice(-5)
            .map((r) => `  ${r.file}: ${r.outcome}${r.error ? ` (${r.error})` : ""}`).join("\n");
        const details = recent ? `\n\nMost recent results:\n${recent}` : "";
        if (state?.status === "running") {
            this.backfillStatus.textContent = `Working… ${state.done || 0} / ${state.total || "?"}`
                + `${state.current ? ` — ${state.current}` : ""}\n${summary}`;
        } else if (state?.status === "interrupted") {
            // The server restarted mid-run: nothing is actually working any
            // more, so the button must not stay disabled and polling must
            // not continue - starting again is the only way forward.
            this.backfillStatus.textContent = `Interrupted at ${state.done || 0} / ${state.total || "?"}`
                + ` — run again to continue\n${summary}${details}`;
        } else if (state?.finished_at) {
            const when = new Date(state.finished_at * 1000).toLocaleString();
            const label = state.status === "error" ? `failed: ${state.error}` : state.status;
            const restored = state.from_report ? " (restored after a restart)" : "";
            this.backfillStatus.textContent = `Last run ${label} (${when})${restored}\n${summary}${details}`;
        } else {
            this.backfillStatus.textContent = "";
        }
        this.backfillButton.disabled = state?.status === "running";
        if (state?.status === "running" && this.element.style.display !== "none") {
            this.schedulePoll();
        } else {
            clearTimeout(this.backfillTimer);
        }
    }

    schedulePoll() {
        clearTimeout(this.backfillTimer);
        this.backfillTimer = setTimeout(async () => {
            try {
                this.renderBackfill(await this.request("/model_linker/lm-backfill"));
            } catch (e) {
                // A transient failure (the server restarting, a dropped
                // connection) must not stop polling for good - try again,
                // but only while the dialog the user would see it in is
                // still open; otherwise this would poll forever in the
                // background for no one.
                if (this.element.style.display !== "none") {
                    this.schedulePoll();
                }
            }
        }, 2000);
    }

    async startBackfill() {
        try {
            const result = await this.request("/model_linker/lm-backfill/start", { use_ai: this.useAi.checked });
            this.renderBackfill(result.state);
        } catch (error) {
            notifyError("Could not start filling model info", error);
        }
    }

    render(view) {
        for (const p of PROVIDERS) {
            const row = this.rows[p.id];
            const v = view?.[p.id] || {};
            row.input.value = "";
            row.input.placeholder = v.source === "env"
                ? `Using the ${v.env_var} environment variable (${v.hint})`
                : v.set ? `Saved (${v.hint})` : "Not set";
            row.checkBtn.disabled = !v.set;
            row.clear.disabled = v.source !== "settings";
        }
        this.autoLookup.checked = view?.options?.auto_lookup !== false;
    }

    async request(path, body) {
        const response = await api.fetchApi(path, body === undefined ? {} : {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data?.error || `HTTP ${response.status}`);
        return data;
    }

    async show() {
        this.element.style.display = "flex";
        try {
            this.render(await this.request("/model_linker/settings"));
            this.renderBackfill(await this.request("/model_linker/lm-backfill"));
        } catch (error) {
            notifyError("Could not load settings", error);
        }
    }

    close() {
        this.element.style.display = "none";
    }

    async save(updates, thenCheck = null) {
        try {
            this.render(await this.request("/model_linker/settings", updates));
            if (thenCheck) await this.check(thenCheck);
        } catch (error) {
            notifyError("Could not save settings", error);
        }
    }

    async check(provider) {
        const { status } = this.rows[provider];
        status.textContent = "Checking…";
        try {
            const result = await this.request(`/model_linker/settings/check/${provider}`, {});
            status.textContent = result.ok
                ? `✓ Valid${result.user ? ` — signed in as ${result.user}` : ""}`
                : `✗ ${result.error}`;
        } catch (error) {
            status.textContent = `✗ ${error.message}`;
        }
    }
}
