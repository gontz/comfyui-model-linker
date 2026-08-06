/**
 * Helpers with no DOM or ComfyUI dependency.
 *
 * Kept apart from the dialogs so they can be imported and tested directly,
 * rather than reached by evaluating the whole extension.
 */

import { app } from "../../../scripts/app.js";

/**
 * Extensions that are never a model, whatever folder they sit in.
 *
 * Preview images, sample videos and metadata sidecars live right beside the
 * models they describe, and a category can declare extensions broadly enough to
 * sweep them up. None of them is ever a valid replacement for a model, so the
 * picker refuses to offer them regardless of how they were catalogued.
 */
export const NON_MODEL_EXTENSIONS = new Set([
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg", ".avif",
    ".mp4", ".webm", ".mov", ".avi", ".mkv", ".gifv",
    ".info", ".lock", ".md", ".html", ".csv", ".log",
]);

/** Whether a catalogued entry may be offered as a model replacement. */
export function isSelectableModel(model) {
    const name = (model?.filename || model?.relative_path || "").toLowerCase();
    const dot = name.lastIndexOf(".");
    if (dot < 0) return true;
    return !NON_MODEL_EXTENSIONS.has(name.slice(dot));
}

/**
 * Identify one model reference uniquely.
 *
 * A node can hold several references in a single widget: Lora Manager keeps a
 * list of loras in one slot, so node id and widget index alone are ambiguous
 * and `list_index` is what separates them. Every element id and every pending
 * selection is keyed through here so those entries cannot collide.
 */
export function refSlot(ref) {
    const list = (ref.list_index === undefined || ref.list_index === null) ? 'n' : ref.list_index;
    return `${ref.node_id}-${ref.widget_index}-${list}-${ref.subgraph_id || 'top'}`;
}

/** Stable key for a reference, used to deduplicate queued selections. */
export function refKey(ref) {
    const list = (ref.list_index === undefined || ref.list_index === null) ? '' : ref.list_index;
    return `${ref.node_id}:${ref.widget_index}:${list}:${ref.subgraph_id || ''}:${ref.is_top_level ? 'T' : 'F'}`;
}

/** Escape a value for interpolation into an HTML string. */
export function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

/**
 * Return a URL only if it is a plain web link.
 *
 * Download URLs arrive inside workflow files, which are shared and downloaded
 * freely, so they are untrusted input. Restricting them to http(s) keeps a
 * javascript: or data: URL from becoming a clickable link in the dialog.
 */
export function safeHttpUrl(value) {
    if (!value || typeof value !== "string") return null;
    try {
        const url = new URL(value);
        return (url.protocol === "http:" || url.protocol === "https:") ? url.href : null;
    } catch (e) {
        return null;
    }
}

/**
 * Surface an error to the user through ComfyUI's toast system.
 *
 * Replaces window.alert, which blocks the page and cannot be styled or
 * dismissed programmatically. Falls back to the console if toasts are
 * unavailable, so nothing is ever swallowed silently.
 */
export function notifyError(summary, error) {
    const detail = error?.message || String(error || "");
    try {
        app.extensionManager?.toast?.add({
            severity: "error",
            summary: `Model Linker: ${summary}`,
            detail,
            life: 6000,
        });
        return;
    } catch (e) {
        // fall through to the console
    }
    console.error(`Model Linker: ${summary}`, error);
}
