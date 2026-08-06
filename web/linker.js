/**
 * ComfyUI Model Linker Extension - Frontend
 *
 * Relinks missing models in a workflow. This file is only the entry point:
 * it registers the extension with ComfyUI and opens the dialog. The dialog
 * itself, the overrides manager and the shared helpers live in ./modules/.
 */

import { app } from "../../scripts/app.js";

import { LinkerManagerDialog } from "./modules/linker-dialog.js";
import { notifyError } from "./modules/util.js";

// Main extension class
class ModelLinker {
    constructor() {
        this.dialog = null;
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
}

const modelLinker = new ModelLinker();

// Register the extension
app.registerExtension({
    name: "Model Linker",
    setup: modelLinker.setup,


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
