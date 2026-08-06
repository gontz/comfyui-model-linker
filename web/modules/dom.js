/**
 * Minimal element builder.
 *
 * Deliberately local rather than imported from "scripts/ui.js": that module is
 * deprecated and was announced for removal in frontend v1.34. Only
 * scripts/app.js and scripts/api.js are safe to import from ComfyUI.
 */

/**
 * Build a DOM element, mirroring ComfyUI's own $el helper.
 *
 * Kept local rather than imported from "scripts/ui.js": that module logs a
 * deprecation notice and was announced for removal in frontend v1.34, which
 * current releases are well past. Its ComfyDialog base class was the only other
 * thing used here, and both dialogs below build and manage their own element,
 * so nothing of it is needed.
 *
 * @param {string} tag Tag name, optionally with .classes appended
 * @param {object|string|Element|Array} [propsOrChildren] Properties to assign,
 *   or text/children when no properties are needed. `parent` appends the result,
 *   `style` and `dataset` are merged, and `$` is called with the element.
 * @param {Array|Element} [children] Children, when properties were given
 */
export function $el(tag, propsOrChildren, children) {
    const parts = tag.split(".");
    const element = document.createElement(parts.shift());
    if (parts.length > 0) element.classList.add(...parts);

    if (!propsOrChildren) return element;

    if (typeof propsOrChildren === "string") {
        propsOrChildren = { textContent: propsOrChildren };
    } else if (propsOrChildren instanceof Element) {
        propsOrChildren = [propsOrChildren];
    }

    if (Array.isArray(propsOrChildren)) {
        element.append(...propsOrChildren);
        return element;
    }

    const { parent, $: onCreate, dataset, style, ...rest } = propsOrChildren;
    if (rest.for) element.setAttribute("for", rest.for);
    if (style) Object.assign(element.style, style);
    if (dataset) Object.assign(element.dataset, dataset);
    Object.assign(element, rest);
    if (children) element.append(...(Array.isArray(children) ? children : [children]));
    if (parent) parent.append(element);
    if (onCreate) onCreate(element);

    return element;
}
