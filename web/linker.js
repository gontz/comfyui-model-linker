/**
 * ComfyUI Model Linker Extension - Frontend
 *
 * Relinks missing models in a workflow. This file is only the entry point:
 * it registers the extension with ComfyUI and opens the dialog. The dialog
 * itself, the overrides manager and the shared helpers live in ./modules/.
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

import { LinkerManagerDialog } from "./modules/linker-dialog.js";
import { notifyError } from "./modules/util.js";

// Main extension class
export class ModelLinker {
    constructor() {
        this.dialog = null;
        this.notified = new Set();
        this.loadTimer = null;
        this.loadGeneration = 0;
    }

    // Model Linker is reached from the View menu, the command palette, the
    // Alt+L shortcut and the canvas right-click menu - all registered below.
    // It deliberately adds no control of its own: earlier versions searched
    // ComfyUI's markup for any element whose class named it a menu and appended
    // a button to the first one found. Against a Vue-rendered interface that
    // landed somewhere arbitrary and broke whenever the markup shifted.
    setup = () => {
        if (!this.dialog) {
            this.dialog = new LinkerManagerDialog();
        }
    }

    openLinkerManager() {
        try {
            if (!this.dialog) {
                this.dialog = new LinkerManagerDialog();
            }
            this.dialog.show();
        } catch (error) {
            console.error("🔗 Model Linker: Error creating/showing dialog:", error);
            notifyError("Could not open Model Linker", error);
        }
    }

    openExport() {
        try {
            if (!this.dialog) this.dialog = new LinkerManagerDialog();
            // show() starts an analysis; openExport() starts a newer one, which
            // supersedes it and is the one the Export dialog is built from
            this.dialog.show();
            this.dialog.openExport();
        } catch (error) {
            console.error("🔗 Model Linker: Error opening Export:", error);
            notifyError("Could not open Export", error);
        }
    }

    // ComfyUI fires afterConfigureGraph for every graph it loads, including
    // tab switches, so the check is debounced and each workflow's missing set
    // is announced once per session. A workflow the Export dialog was built
    // from is one of these, so it is closed immediately rather than left
    // open to write by node id into a tab that is no longer the one on screen.
    onGraphLoaded() {
        this.dialog?.exportDialog?.close();
        clearTimeout(this.loadTimer);
        this.loadTimer = setTimeout(() => this.checkLoadedWorkflow(), 500);
    }

    // app.graph.serialize() carries no id for the open tab - LiteGraph's
    // serialized graph has no such field. ComfyUI's own workflow store does:
    // every open tab is a ComfyWorkflow keyed by its own (disambiguated) path.
    workflowKey() {
        return app.extensionManager?.workflow?.activeWorkflow?.path;
    }

    async checkLoadedWorkflow() {
        // The first workflow can load before setup() has created the dialog
        if (!this.dialog) this.dialog = new LinkerManagerDialog();
        if (this.dialog.isOpen()) return;
        const workflow = this.dialog.getCurrentWorkflow();
        if (!workflow?.nodes?.length) return;
        // Read together with the workflow: if the user switches tabs while
        // the request below is in flight, the active workflow by the time it
        // resolves is not the one this check is about.
        const workflowKey = this.workflowKey();
        // A slow analysis must not announce stale results once another
        // workflow has loaded in the meantime - the same kind of guard
        // loadWorkflowData uses against a superseded analysis, via a
        // generation counter instead of an AbortController since this
        // request has no dialog UI waiting on it to cancel.
        const generation = ++this.loadGeneration;
        let data;
        try {
            const response = await api.fetchApi("/model_linker/analyze", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ workflow }),
            });
            if (!response.ok) {
                console.debug("Model Linker: background check failed", response.status);
                return;
            }
            data = await response.json();
        } catch (error) {
            console.debug("Model Linker: background check failed", error);
            return;  // a background courtesy; the dialog reports real errors
        }
        if (generation !== this.loadGeneration) return;
        this.announce(workflowKey, data);
    }

    /** Toast or open the dialog for one analysis result, once per workflow+missing-set. */
    announce(workflowKey, data) {
        const count = data.total_missing || 0;
        if (!count) return;
        const missingKey = (data.missing_models || []).map(m => m.original_path).sort().join("|");
        // Falls back to the missing-set alone when no workflow identity is
        // available (e.g. an older frontend with no workflow store), rather
        // than silently colliding every workflow under the same blank key.
        const signature = workflowKey ? `${workflowKey}:${missingKey}` : missingKey;
        if (this.notified.has(signature)) return;
        this.notified.add(signature);

        if (app.extensionManager?.setting?.get?.("ModelLinker.OpenOnMissing")) {
            this.openLinkerManager();
            return;
        }
        const searching = data.lookups_pending ? " Looking for downloads in the background." : "";
        app.extensionManager?.toast?.add({
            severity: "warn",
            summary: "Model Linker",
            detail: `${count} model${count === 1 ? " is" : "s are"} missing. Press Alt+L to relink.${searching}`,
            life: 8000,
        });
    }
}

const modelLinker = new ModelLinker();

// Register the extension
app.registerExtension({
    name: "Model Linker",
    setup: modelLinker.setup,
    afterConfigureGraph: () => modelLinker.onGraphLoaded(),
    nodeCreated(node) {
        if (node.comfyClass !== "ModelLinker") return;
        for (const [label, action] of [["Open Model Linker", () => modelLinker.openLinkerManager()],
                                       ["Export…", () => modelLinker.openExport()]]) {
            // options.serialize is what graphToPrompt checks; the widget's own
            // flag keeps the buttons out of widgets_values. Setting the latter
            // after addWidget is fine: the saved-value restore addWidget runs
            // first means nothing for a button.
            const widget = node.addWidget("button", label, null, action, { serialize: false });
            widget.serialize = false;
        }
        node.size = [220, node.computeSize()[1]];
    },
    settings: [
        {
            id: "ModelLinker.OpenOnMissing",
            category: ["Model Linker", "Workflow loading", "Open on missing models"],
            name: "Open Model Linker when a loaded workflow has missing models",
            tooltip: "Otherwise a notification says how many are missing.",
            type: "boolean",
            defaultValue: false,
        },
    ],

    // Registered as a command so it appears in the command palette and can be
    // bound to a key, rather than existing only as a button in the page
    commands: [
        {
            id: "model-linker-open",
            icon: "🔗",
            label: "Model Linker",
            function: () => modelLinker.openLinkerManager()
        }
    ],
    menuCommands: [
        {
            path: ["View"],
            commands: ["model-linker-open"]
        }
    ],
    keybindings: [
        {
            commandId: "model-linker-open",
            combo: { key: "l", alt: true }
        }
    ],
    // Add to canvas right-click menu
    getCanvasMenuItems() {
        return [
            {
                content: "🔗 Model Linker",
                callback: () => modelLinker.openLinkerManager()
            }
        ];
    }
});
