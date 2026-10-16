# Plan C — Workflow UI, Export and Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Locate nodes inside nested subgraphs, swap any model a workflow uses (not only missing ones), notice missing models as soon as a workflow loads, export a shareable workflow with download links, and bring the project documentation in line with the new download feature.

**Architecture:** Locate gains `liveNodesFor(ref)` and `focusNode(node)` (the latter ported from upstream, which mirrors the frontend's own locate). The existing "Models found on disk" list becomes swappable by reusing the missing-model picker through a new `onChoose` callback on `attachModelCombo` — one queued resolution per referencing node, applied through the existing `/resolve`. Load-time notice uses the `afterConfigureGraph` extension hook instead of upstream's DOM-scraping popup injection. Export is a separate modal (`web/modules/export-dialog.js`) backed by upstream's `core/exporter.py` and new `core/routes_export.py`, plus a never-executing `ModelLinker` node that carries buttons.

**Tech Stack:** ES modules, LiteGraph (via ComfyUI's `app`), aiohttp, `python tests/run.py`.

**Spec:** `docs/superpowers/specs/2026-10-14-upstream-parity-design.md`

**Requires Plans A and B** — `isPerfectMatch`, `SettingsDialog`/`openSettings()`, `downloads` controller, `source_chain.find_download_source`/`find_by_hash`, `settings.get_option`, the guarded registration block in `__init__.py`, and Plan A's sanitised `refSlot()` (output limited to `[A-Za-z0-9_-]`, so it is safe in `id` attributes and `#…` selectors; this plan relies on it and does not re-escape it).

## Revisions (2026-10-16)

From a review against the code and frontend 1.55.17:

- Export: every row's URL goes through `/export/lookup` as `existing_url` (stripped server-side, no network), and `apply()` re-strips `linked` through the same route before writing; pinned by `lookupPayload` and order checks in `test_export.mjs`.
- Export: `loadWorkflowData()` returns its analysis (`null` when superseded or empty); `openExport()` always awaits a fresh one; `analyzeData` dropped.
- Undo: `app.canvas?.emitBeforeChange?.()`/`emitAfterChange?.()` replace `graph.beforeChange/afterChange` (no-ops for undo in 1.55); `updateWorkflowInComfyUI` is wrapped too; test regexes updated.
- The Model Linker marker node is muted (`mode = 2`), so `graphToPrompt` leaves it out and exported workflows run without this extension; button widgets get `{ serialize: false }` as an `addWidget` option.
- `present_models` carries `source_url`/`source_hash`/`source_hash_type`; `exportRows` takes a hash only when its type is `sha256`.
- Export respects `auto_lookup`: `/export/lookup` searches only when it is on or the request is a manual "Find links" press; `lookup_links` gained `search`. Index alignment kept for malformed entries.
- Swap: the panel's Remove drops the whole group; swap resolutions carry `record_override: false`, which `/resolve` honours via `overrides.resolutions_to_record()` (new Python test).
- Task 1: Step 2/5 expectations corrected (unused subgraph definitions are pruned on serialize); "toast" → notification banner.
- Task 3: the dialog is created lazily in `checkLoadedWorkflow`.
- Task 4: the exporter test stub has no `**kw`, so a surviving `fetch_size=False` fails a test.
- Task 5: complete module/test/route lists from A–C; README covers Search similar and the HuggingFace backfill; CLAUDE.md lines 9, 15–17, 46 and the hook list fixed; memory edits retargeted.

## Global Constraints

- Only `scripts/app.js` and `scripts/api.js` may be imported from ComfyUI; never `scripts/ui.js` (`tests/js/test_extension.mjs` enforces it).
- Never scrape ComfyUI's markup for an injection point; register through extension hooks (`commands`, `settings`, `afterConfigureGraph`, `nodeCreated`, `getCanvasMenuItems`). No `MutationObserver` on `document`.
- No `alert()`/`confirm()`; toasts via `app.extensionManager.toast`.
- Escape every workflow- or network-derived string put into HTML; links through `safeHttpUrl()`.
- Element ids and queue keys for references are built with `refSlot()`/`refKey()` only.
- Every graph mutation the user triggers is one undo step: `app.canvas?.emitBeforeChange?.()` … `app.canvas?.emitAfterChange?.()`, the second in a `finally`. Not `graph.beforeChange()/afterChange()`: in frontend 1.55 those only call `canvas.onBeforeChange`, which nothing assigns, and the root graph has no canvas attached while a subgraph is open; ComfyUI's ChangeTracker listens for the canvas's `litegraph:canvas` before/after-change events.
- `python tests/run.py` is green after every task, where `python` means `C:/ComfyUI/.venv/Scripts/python.exe`.

## Review Focus

- A reference whose subgraph has **no** live instance on the canvas (a stale analysis — `serialize()` drops unused definitions, so a fresh one never lists it): Locate must say so in the dialog's notification banner (`showNotification`), not throw. Pinned in Task 1 (source check for the empty-result message).
- Swapping a model used by four nodes must queue four resolutions, the queue's Apply must update all four, Remove must drop all four, and no override may be saved. Pinned in Task 2.
- Switching between already-open workflow tabs must not toast "N models missing" every time. Pinned in Task 3 (per-workflow dedupe check).
- A URL carrying `?token=…` — from the workflow or pasted into a row — must not be written into the shared workflow by Export, in the note or in `properties.models`. Pinned in Task 4 (`lookupPayload` sends every row's URL as `existing_url`; `apply()` calls `/export/lookup` before it writes; `test_existing_link_wins_and_is_stripped`).
- Export must not send filenames online when `auto_lookup` is off, unless the user presses "Find links". Pinned in Task 4 (`test_no_search_without_permission`).
- Export must describe the workflow on screen, never an earlier analysis. Pinned in Task 4 (`openExport` awaits `loadWorkflowData()`).
- Export applied twice must update the existing "Model links" note and Model Linker node, not add duplicates; the Model Linker node is muted so the shared workflow runs without this extension. Pinned in Task 4.

---

### Task 1: Locate goes into nested subgraphs

**Files:**
- Modify: `web/modules/linker-dialog.js` (`renderMissingModel` header rows ~1279–1306; Locate wiring ~1253–1258; replace `locateNodeInGraph` ~2109–2146; keep `findLiveNodeInSubgraphs`)
- Test: `tests/js/test_locate.mjs` (new)

**Interfaces:**
- Consumes: `findLiveNodeInSubgraphs(graph, subgraphId, nodeId)` (existing).
- Produces: `liveNodesFor(ref) -> LGraphNode[]` (Task 4 writes `properties.models` through it); `async focusNode(node)`; `async locateRef(ref)`.

- [ ] **Step 1: Write the failing test**

Create `tests/js/test_locate.mjs`:

```js
// Locate must reach nodes inside (nested) subgraphs - entering the subgraph
// the way ComfyUI's own locate button does - rather than refusing.
import { createChecker, readSource } from './harness.mjs';

export const name = 'locate';

export default async function run() {
  const check = createChecker(name);
  const src = readSource('modules/linker-dialog.js');
  check('no disabled "Located in subgraph" button remains', !/Located in subgraph/.test(src));
  check('focusNode enters the node\'s own graph', /async focusNode\(node\)[\s\S]{0,700}setGraph\(/.test(src));
  check('focusNode animates like the frontend does', /animateToBounds/.test(src));
  check('locate works from a reference, not a bare id', /async locateRef\(ref\)/.test(src));
  check('a subgraph without a live instance is reported',
        /has no live instance/.test(src.slice(src.indexOf('async locateRef'))));
  check('one helper finds live nodes for a reference', /liveNodesFor\(ref\)\s*\{/.test(src));
  return check.failures;
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `node tests/js/run.mjs` — expected: FAIL on all six `locate` checks (the disabled "Located in subgraph" button is still there, so the first fails too).

- [ ] **Step 3: Implement**

Replace the whole `locateNodeInGraph(nodeId)` method with:

```js
    /** The live canvas node(s) a reference points at. Subgraph instances share one definition. */
    liveNodesFor(ref) {
        if (!app?.graph) return [];
        const node = ref.is_top_level === false
            ? this.findLiveNodeInSubgraphs(app.graph, ref.subgraph_id, ref.node_id)
            : (app.graph.getNodeById?.(ref.node_id) || app.graph._nodes_by_id?.[ref.node_id]);
        return node ? [node] : [];
    }

    /**
     * Move the viewport onto a live node, entering its subgraph first when the
     * canvas shows another graph. Ported from upstream, which mirrors the
     * frontend's useFocusNode (the locate button in ComfyUI's workflow
     * overview). Older frontends lack setGraph/animateToBounds; both degrade.
     */
    async focusNode(node) {
        const canvas = app.canvas;
        const graph = node.graph;
        if (graph && canvas.graph !== graph && typeof canvas.setGraph === 'function') {
            const isRoot = graph.isRootGraph ?? (graph === (app.rootGraph || app.graph));
            canvas.subgraph = isRoot ? undefined : graph;
            canvas.setGraph(graph);
            // LiteGraph needs a full frame in the new graph before measuring
            await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        }
        if (typeof canvas.animateToBounds === 'function' && node.boundingRect) {
            canvas.animateToBounds(node.boundingRect);
        } else {
            canvas.centerOnNode?.(node);
        }
        if (typeof canvas.selectNode === 'function') canvas.selectNode(node);
        else canvas.selectNodes?.([node]);
        canvas.setDirty?.(true, true);
    }

    async locateRef(ref) {
        if (!app?.graph || !app?.canvas) {
            this.showNotification('Canvas not available', 'error');
            return;
        }
        const [node] = this.liveNodesFor(ref);
        if (!node) {
            const where = ref.subgraph_name || ref.subgraph_id;
            this.showNotification(where
                ? `Node #${ref.node_id} not found - subgraph "${where}" has no live instance on the canvas`
                : `Node #${ref.node_id} not found on the canvas`, 'error');
            return;
        }
        this.close();  // the canvas has to be visible to be of any use
        try {
            await this.focusNode(node);
        } catch (error) {
            console.error('Model Linker: locate failed', error);
        }
    }
```

In `renderMissingModel`, each of the three header branches has an `if (missing.is_top_level === false) { …disabled "Located in subgraph" button… } else { …Locate button… }`. Replace each whole `if/else` with just:

```js
            html += `<button id="${locateId}" class="model-linker-resolve-btn" style="padding:2px 8px;">Locate</button>`;
```

In the wiring (around line 1256), replace

```js
            if (locateBtn && missing.is_top_level !== false) {
                locateBtn.addEventListener('click', () => this.locateNodeInGraph(missing.node_id));
            }
```

with

```js
            if (locateBtn) {
                locateBtn.addEventListener('click', () => this.locateRef(missing));
            }
```

Then `grep -n "locateNodeInGraph" web/` must return nothing; fix any remaining caller to `locateRef(ref)`.

- [ ] **Step 4: Run the whole suite**

Run: `python tests/run.py` — expected: all pass.

- [ ] **Step 5: Verify in the app**

Hard-refresh. Build a workflow with a `LoraLoader` inside a subgraph inside another subgraph, referencing a missing LoRA. Locate: the dialog closes, the canvas enters the inner subgraph and frames the loader.

The "no live instance" message only shows for a stale analysis: `app.graph.serialize()` drops subgraph definitions nothing uses, so reopening the dialog after deleting the outer node no longer lists the LoRA at all. To see it, open the dialog and leave it open, remove the outer subgraph node from the browser console (`app.graph.remove(app.graph.getNodeById(<outer id>))`), then press Locate on the card: the notification banner says the subgraph has no live instance, and the dialog stays open.

- [ ] **Step 6: Commit**

```bash
git add web/modules/linker-dialog.js tests/js/test_locate.mjs
git commit -m "Locate nodes inside nested subgraphs"
```

---

### Task 2: Swap any model the workflow uses

**Files:**
- Modify: `web/modules/linker-dialog.js` (`attachModelCombo` signature and its two `queueResolution` calls ~2061 and ~2076; `queueResolution` ~1640; `renderPresentModels` 1095–1124; `attachPresentModelHandlers` 1127–1137; `updateWorkflowInComfyUI` ~2221), `core/overrides.py` (new `resolutions_to_record`), `__init__.py` (`/resolve` override loop, line ~167)
- Test: `tests/js/test_swap.mjs` (new), `tests/test_overrides.py`

**Interfaces:**
- Consumes: `present_models` from `/analyze` (one entry per reference, with `node_id, widget_index, original_path, category, canonical_category, nested_key, list_index, adapter_id, subgraph_id, subgraph_name, is_top_level, node_type`); `queueResolution(ref, model)`; `removeQueuedResolution(ref)`; `ensureAllModelsLoaded()`.
- Produces: `attachModelCombo(container, missing, onChoose = (model) => this.queueResolution(missing, model))`; exported pure helper `groupPresentModels(present) -> [{key, name, category, refs: [...]}]` in `web/modules/util.js`; resolution field `record_override` (default true; a swap sends false); `overrides.resolutions_to_record(resolutions) -> list`.

`/resolve` already takes one resolution per reference, `apply_resolution` does not care whether the reference was missing, and `present_models` already carries every field a resolution needs. The one backend change: a swap is not a correction of a missing model, so it must not be remembered as an override — otherwise swapping present A for B saves `category:A → B`, and if A ever goes missing B is offered as a 100% "saved" match.

- [ ] **Step 1: Write the failing test**

Create `tests/js/test_swap.mjs`:

```js
// One model used by several nodes is one row, and swapping it queues a
// resolution for every one of those nodes.
import { createChecker, loadModule, readSource } from './harness.mjs';

export const name = 'swap';

export default async function run() {
  const check = createChecker(name);
  const { groupPresentModels } = await loadModule('modules/util.js');
  const ref = (id, path, category = 'loras', extra = {}) =>
    ({ node_id: id, widget_index: 0, original_path: path, category, canonical_category: category, ...extra });

  const groups = groupPresentModels([
    ref(1, 'a.safetensors'), ref(2, 'a.safetensors'), ref(3, 'b.safetensors'),
    ref(4, 'a.safetensors', 'clip', { canonical_category: 'text_encoders' }),
    ref(5, 'x.safetensors', 'loras', { list_index: 0 }), ref(5, 'x.safetensors', 'loras', { list_index: 1 }),
  ]);
  const a = groups.find(g => g.name === 'a.safetensors' && g.category === 'loras');
  check('same file and folder group together', a && a.refs.length === 2);
  check('the same name in another folder is a separate row',
        groups.filter(g => g.name === 'a.safetensors').length === 2);
  check('two list entries on one node stay two references',
        groups.find(g => g.name === 'x.safetensors').refs.length === 2);

  const src = readSource('modules/linker-dialog.js');
  check('the picker takes a selection callback', /attachModelCombo\(container, missing, onChoose/.test(src));
  check('the picker no longer queues directly', (src.match(/this\.queueResolution\(missing, chosen\)/g) || []).length === 0);
  check('a swap queues every referencing node',
        /group\.refs\.forEach\(\(ref\) => this\.queueResolution\(\{ \.\.\.ref, record_override: false \}, model\)\)/.test(src));
  check('removing a swap removes it from every node',
        /group\.refs\.forEach\(\(ref\) => this\.removeQueuedResolution\(ref\)\)/.test(src));
  check('a resolution says whether to remember it', /record_override: missing\.record_override !== false/.test(src));
  check('applying selections is one undo step',
        /updateWorkflowInComfyUI\(workflow, resolutions\) \{[\s\S]{0,1200}emitBeforeChange\?\.\(\)[\s\S]*?emitAfterChange\?\.\(\)/.test(src));
  return check.failures;
}
```

Append to `tests/test_overrides.py` (it already imports `os` and `unittest`):

```python
class RecordOverrideFlagTests(unittest.TestCase):
    """A swap replaces a model that is present; it is not a fix worth remembering."""

    def test_swaps_are_not_recorded(self):
        from core.overrides import resolutions_to_record
        kept = resolutions_to_record([
            {'original_path': 'a.safetensors'},
            {'original_path': 'b.safetensors', 'record_override': False},
            {'original_path': 'c.safetensors', 'record_override': True},
            'not a resolution',
        ])
        self.assertEqual([r['original_path'] for r in kept], ['a.safetensors', 'c.safetensors'])

    def test_resolve_route_uses_the_filter(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, '__init__.py'), encoding='utf-8') as handle:
            self.assertIn('for res in resolutions_to_record(resolutions):', handle.read())
```

- [ ] **Step 2: Run to verify it fails**

Run: `node tests/js/run.mjs` — expected: FAIL (`groupPresentModels` is not exported).
Run: `python -m unittest discover -t . -s tests -p "test_overrides.py" -v` — expected: the two `RecordOverrideFlagTests` FAIL (`ImportError`, then the missing text).

- [ ] **Step 3: Add the helper**

Append to `web/modules/util.js`:

```js
/**
 * Present-model references grouped by file: one row per (folder, path), with
 * every reference that loads it. A swap then updates all of them at once.
 */
export function groupPresentModels(present) {
    const groups = new Map();
    for (const ref of present || []) {
        const category = ref.canonical_category || ref.category || "unknown";
        const key = `${category}|${ref.original_path}`;
        if (!groups.has(key)) {
            groups.set(key, {
                key,
                name: (ref.original_path || "").split(/[\\/]/).pop(),
                category,
                refs: [],
            });
        }
        groups.get(key).refs.push(ref);
    }
    return [...groups.values()].sort((a, b) =>
        a.category.localeCompare(b.category) || a.name.localeCompare(b.name));
}
```

- [ ] **Step 4: Give the picker a selection callback**

Change the signature `attachModelCombo(container, missing) {` to

```js
    attachModelCombo(container, missing, onChoose = (model) => this.queueResolution(missing, model)) {
```

and replace both `this.queueResolution(missing, chosen);` lines inside it with `onChoose(chosen);`.

In `queueResolution(missing, resolvedModel)`, add to the `resolution` object, after `adapter_id: missing.adapter_id || null,`:

```js
            // A swap replaces a model that is present; /resolve must not save it as an override
            record_override: missing.record_override !== false,
```

- [ ] **Step 5: Make the present list swappable**

Add `groupPresentModels` to the `./util.js` import. Replace `renderPresentModels(present)` with:

```js
    renderPresentModels(present) {
        const groups = groupPresentModels(present);
        this._presentGroups = groups;
        if (!groups.length) return '';

        let html = `<details id="model-linker-present" style="margin-top:16px;">`
            + `<summary style="cursor:pointer; opacity:.85;">Models found on disk (${groups.length}) — swap any of them</summary>`
            + `<div style="display:flex; flex-direction:column; gap:4px; margin-top:8px;">`;

        groups.forEach((group, index) => {
            const first = group.refs[0];
            const users = group.refs.length === 1
                ? escapeHtml(`${first.node_type || 'Node'} #${first.node_id}${first.subgraph_name ? ` · ${first.subgraph_name}` : ''}`)
                : `${group.refs.length} nodes`;
            html += `<div style="border-bottom:1px solid var(--border-color); padding:4px 6px;">`
                + `<div style="display:flex; align-items:center; gap:8px;">`
                + `<code style="flex:1 1 auto; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHtml(first.original_path)}">${escapeHtml(group.name)}</code>`
                + `<span style="flex:0 0 auto; opacity:.75; font-size:12px;">[${escapeHtml(group.category)}]</span>`
                + `<span style="flex:0 0 auto; opacity:.6; font-size:12px;">${users}</span>`
                + `<button id="present-swap-${index}" class="model-linker-resolve-btn" style="flex:0 0 auto; padding:2px 8px;" title="Replace this model in every node that uses it">⇄ Swap</button>`
                + `<button id="present-reveal-${index}" class="model-linker-resolve-btn" style="flex:0 0 auto; padding:2px 8px;" title="Show in the file manager">📂</button>`
                + `</div>`
                + `<div id="present-swap-panel-${index}" style="display:none; padding:6px 0;">`
                + `<div id="selected-${refSlot(first)}" class="model-linker-selected" style="display:none; margin: 4px 0; padding: 6px; border: 1px solid var(--border-color, #444); border-radius: 4px;"></div>`
                + `</div></div>`;
        });

        return html + '</div></details>';
    }
```

Replace `attachPresentModelHandlers(container)` with:

```js
    attachPresentModelHandlers(container) {
        (this._presentGroups || []).forEach((group, index) => {
            const first = group.refs[0];
            const reveal = container.querySelector(`#present-reveal-${index}`);
            if (reveal) {
                reveal.onclick = () => this.revealModel({ category: first.category, relative_path: first.original_path });
            }
            const swap = container.querySelector(`#present-swap-${index}`);
            const panel = container.querySelector(`#present-swap-panel-${index}`);
            if (!swap || !panel) return;
            swap.onclick = async () => {
                const opening = panel.style.display === 'none';
                panel.style.display = opening ? '' : 'none';
                if (!opening || panel.dataset.ready) return;
                panel.dataset.ready = '1';
                await this.ensureAllModelsLoaded();
                // A present model has no fuzzy matches; the picker lists the folder
                this.attachModelCombo(panel, { ...first, matches: [] }, (model) => {
                    group.refs.forEach((ref) => this.queueResolution({ ...ref, record_override: false }, model));
                    // The bar shows the first reference only; its Remove must
                    // drop the swap from every node, not just that one
                    const remove = panel.querySelector(`#selected-remove-${refSlot(first)}`);
                    if (remove) remove.onclick = () => group.refs.forEach((ref) => this.removeQueuedResolution(ref));
                });
            };
        });
    }
```

`displayMissingModels` already calls `attachPresentModelHandlers(container)` in both branches (lines 1148 and 1186), so nothing else changes there. `refSlot(first)` is safe in a selector because Plan A sanitises it.

- [ ] **Step 5b: Make applying selections one undo step**

In `updateWorkflowInComfyUI(workflow, resolutions)`, directly inside `if (resolutions && resolutions.length > 0) {`, add `app.canvas?.emitBeforeChange?.();` and wrap the rest of that block — from `let updatedCount = 0;` through its `return;` — in `try { … } finally { app.canvas?.emitAfterChange?.(); }`. This covers Swap, the queue's Apply and auto-resolve, which all go through it.

- [ ] **Step 5c: Do not record swaps as overrides**

Append to `core/overrides.py`:

```python
def resolutions_to_record(resolutions: Any) -> List[Dict[str, Any]]:
    """
    The resolutions worth remembering as overrides. A swap of a model that is
    present sends `record_override: False`: it is a preference for this
    workflow, not the answer to "where did this missing model go".
    """
    return [r for r in (resolutions or [])
            if isinstance(r, dict) and r.get('record_override') is not False]
```

In `__init__.py`'s `/resolve` handler, add `resolutions_to_record,` to the `from .core.overrides import (...)` list, and in the override block change `for res in resolutions:` to `for res in resolutions_to_record(resolutions):`. `apply_resolution` still receives every resolution.

- [ ] **Step 6: Run the whole suite**

Run: `python tests/run.py` — expected: all pass.

- [ ] **Step 7: Verify in the app**

Restart ComfyUI (the `/resolve` change) and hard-refresh. Load a workflow with one LoRA used by three `LoraLoader` nodes (all present on disk). Open the dialog → "Models found on disk" → the LoRA is one row saying "3 nodes". ⇄ Swap → pick another LoRA → the queue panel lists three entries → the row's Remove empties the queue; pick again → Apply: all three nodes show the new LoRA; one Ctrl+Z restores all three. Open Overrides: no entry was added for the original LoRA.

- [ ] **Step 8: Commit**

```bash
git add web/modules/util.js web/modules/linker-dialog.js core/overrides.py __init__.py tests/js/test_swap.mjs tests/test_overrides.py
git commit -m "Swap any model a workflow uses, across every node that loads it, without saving an override"
```

---

### Task 3: Notice missing models when a workflow loads

**Files:**
- Modify: `web/linker.js` (imports, `ModelLinker`, `registerExtension` config), `web/modules/linker-dialog.js` (`isOpen()`)
- Test: `tests/js/test_extension.mjs`

**Interfaces:**
- Consumes: `/model_linker/analyze` (Plan B makes it queue lookups as a side effect); `dialog.getCurrentWorkflow()`.
- Produces: setting `ModelLinker.OpenOnMissing` (boolean, default false); `LinkerManagerDialog.isOpen() -> boolean`.

Upstream injects a button into ComfyUI's own Missing Models popup with a document-wide `MutationObserver` that matches English heading text. That is not ported: it breaks on any localisation or rewording, and CLAUDE.md forbids scraping the Vue-rendered page. The extension hook achieves the same — and, because analysis queues lookups, downloads are being found before the user even opens the dialog.

- [ ] **Step 1: Write the failing checks**

In `tests/js/test_extension.mjs`, before `return check.failures;`, add:

```js
  check('a loaded workflow is checked through the extension hook',
        typeof config.afterConfigureGraph === 'function');
  const openSetting = config.settings?.find?.(s => s.id === 'ModelLinker.OpenOnMissing');
  check('opening on load is an opt-in setting', openSetting?.type === 'boolean' && openSetting?.defaultValue === false);
  check('ComfyUI\'s Missing Models popup is not observed',
        !/MutationObserver/.test(readAllSources()));
  check('the same workflow is announced once per session', /notified\.has\(/.test(source));
  check('a workflow loaded before setup is still checked',
        /if \(!this\.dialog\) this\.dialog = new LinkerManagerDialog\(\);\s*if \(this\.dialog\.isOpen\(\)\) return;/.test(source));
```

(`readAllSources` must be imported from `./harness.mjs` in that file if it is not already.)

- [ ] **Step 2: Run to verify they fail**

Run: `node tests/js/run.mjs` — expected: FAIL on the hook, the setting, the dedupe and the lazy-dialog checks (the `MutationObserver` check already passes).

- [ ] **Step 3: Implement**

In `web/modules/linker-dialog.js` add next to `close()`:

```js
    isOpen() {
        return this.element?.style.display !== "none";
    }
```

In `web/linker.js`:

1. Add `import { api } from "../../scripts/api.js";` below the `app` import.
2. In `class ModelLinker`'s constructor add `this.notified = new Set(); this.loadTimer = null;`.
3. Add methods to `ModelLinker`:

```js
    // ComfyUI fires afterConfigureGraph for every graph it loads, including
    // tab switches, so the check is debounced and each workflow's missing set
    // is announced once per session.
    onGraphLoaded() {
        clearTimeout(this.loadTimer);
        this.loadTimer = setTimeout(() => this.checkLoadedWorkflow(), 500);
    }

    async checkLoadedWorkflow() {
        // The first workflow can load before setup() has created the dialog
        if (!this.dialog) this.dialog = new LinkerManagerDialog();
        if (this.dialog.isOpen()) return;
        const workflow = this.dialog.getCurrentWorkflow();
        if (!workflow?.nodes?.length) return;
        let data;
        try {
            const response = await api.fetchApi("/model_linker/analyze", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ workflow }),
            });
            if (!response.ok) return;
            data = await response.json();
        } catch (error) {
            return;  // a background courtesy; the dialog reports real errors
        }
        const count = data.total_missing || 0;
        if (!count) return;
        const signature = `${workflow.id || ""}:${(data.missing_models || []).map(m => m.original_path).sort().join("|")}`;
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
```

4. In the `app.registerExtension({...})` config add:

```js
    afterConfigureGraph: () => modelLinker.onGraphLoaded(),
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
```

- [ ] **Step 4: Run the whole suite**

Run: `python tests/run.py` — expected: all pass (the existing "no floating-button setting" check still passes: the new setting id does not match `FloatingButton`).

- [ ] **Step 5: Verify in the app**

Hard-refresh. Load a workflow with two missing models: a warning toast appears once. Switch to another tab and back: no second toast. Enable *Settings → Model Linker → Open Model Linker when…*, load another workflow with missing models: the dialog opens instead.

- [ ] **Step 6: Commit**

```bash
git add web/linker.js web/modules/linker-dialog.js tests/js/test_extension.mjs
git commit -m "Notice missing models when a workflow loads, without scraping ComfyUI's popup"
```

---

### Task 4: Export — download links for sharing, and the Model Linker node

**Files:**
- Create (ported): `core/exporter.py`
- Create: `core/routes_export.py`, `web/modules/export-dialog.js`
- Modify: `__init__.py` (node class; register export routes in the guarded block), `core/linker.py` (`_PRESENT_MODEL_FIELDS`), `web/linker.js` (`nodeCreated`, `openExport`), `web/modules/linker-dialog.js` (`loadWorkflowData` returns its analysis; `openExport`; header "Export…" button)
- Test: `tests/test_exporter.py` (new), `tests/test_node.py` (new), `tests/test_inventory.py`, `tests/js/test_export.mjs` (new)

**Interfaces:**
- Consumes: `source_chain.find_download_source`, `source_chain.find_by_hash` (Plan B; a `workflow_url` is returned as-is without any request), `settings.get_option('auto_lookup')` (Plan A), `reveal.locate_model(category, filename) -> (path|None, reason|None)`, `liveNodesFor(ref)` (Task 1), `loadWorkflowData() -> data|null` (this task).
- Produces:
  - `exporter.strip_secrets(url) -> str`, `exporter.build_model_links_markdown([{name, url, directory}]) -> str`, `exporter.lookup_links(models, find_download_source, search=True) -> [link|None]`, `exporter.start_hash_job(path, find_by_hash) -> job_id`, `exporter.get_hash_job(job_id) -> dict|None`.
  - Routes: `POST /model_linker/export/lookup` `{models:[{filename, category, existing_url?, sha256?}], manual?: bool}` → `{links, searched}` (`links[i]` answers `models[i]`); `POST /model_linker/export/markdown` `{models:[{name,url,directory}]}` → `{markdown}`; `POST /model_linker/export/hash` `{category, filename}` → `{job_id}`; `GET /model_linker/export/hash/{job_id}` → job.
  - Node class `ModelLinker` (no inputs, no outputs, never executes; the marker Export adds is muted).
  - `present_models` entries gain `source_url`, `source_hash`, `source_hash_type` (the workflow's own `properties.models` data).
  - `exportRows(data) -> [{key, name, category, original_path, url, sha256, present, refs}]` and `lookupPayload(rows) -> [{filename, category, sha256?, existing_url?}]` exported from `export-dialog.js`; `ExportDialog` with `show(data)`.
  - `LinkerManagerDialog.loadWorkflowData()` returns the analysis it rendered, or `null` (no workflow, superseded, failed).

- [ ] **Step 1: Port the exporter**

```bash
git show bec5c80:core/exporter.py > core/exporter.py
```

Edit `lookup_links`:
1. Delete the line `fetch_size=False,` (Plan B's chain has no such parameter and never contacts workflow URLs).
2. Change its signature to `def lookup_links(models: List[Dict[str, Any]], find_download_source, search: bool = True) -> List[Optional[Dict[str, Any]]]:` and add to its docstring: "With `search` False nothing is looked up online: a model with a link of its own gets it back stripped, any other gets None."
3. Directly after `existing = strip_secrets(model.get('existing_url') or '')`, insert:

```python
        if not existing and (not search or not filename):
            # Lookups are off (and this is not a manual press), or there is
            # nothing to search for: send nothing anywhere
            results.append(None)
            continue
```

Delete `is_inside_model_dirs` — paths for hashing come from `reveal.locate_model`, which already confines them.

- [ ] **Step 2: Write the failing Python tests**

Create `tests/test_exporter.py`:

```python
"""Export: links fit to share, and hashing confined to model folders."""

import hashlib
import os
import time
import unittest

from core import exporter
from support import LibraryTestCase


class LinkTests(unittest.TestCase):
    def test_secrets_are_stripped(self):
        self.assertEqual(exporter.strip_secrets('https://civitai.com/api/download/models/1?token=abc&type=Model'),
                         'https://civitai.com/api/download/models/1?type=Model')
        self.assertEqual(exporter.strip_secrets('https://x.example/a?SIG=1&Signature=2'), 'https://x.example/a')

    def test_existing_link_wins_and_is_stripped(self):
        # No **kwargs: a leftover fetch_size=False raises TypeError here, which
        # lookup_links swallows into None - and this test fails on it
        def chain(filename, category='', workflow_url='', sha256=''):
            return {'url': workflow_url or 'https://other', 'source': 'workflow', 'url_source': 'workflow'}
        links = exporter.lookup_links([{'filename': 'a.safetensors', 'category': 'loras',
                                        'existing_url': 'https://civitai.com/x?token=secret'}], chain)
        self.assertEqual(links[0]['url'], 'https://civitai.com/x')

    def test_no_search_without_permission(self):
        # Recorded, not raised: lookup_links turns a chain exception into None
        asked = []

        def chain(filename, category='', workflow_url='', sha256=''):
            asked.append((filename, workflow_url))
            return {'url': workflow_url or 'https://found', 'source': 'workflow', 'url_source': 'workflow'}
        links = exporter.lookup_links([
            {'filename': 'a.safetensors', 'category': 'loras'},
            {'filename': 'b.safetensors', 'category': 'loras', 'existing_url': 'https://h/b?token=t'},
            {},
        ], chain, search=False)
        self.assertIsNone(links[0])
        self.assertEqual(links[1]['url'], 'https://h/b')
        self.assertIsNone(links[2])
        self.assertEqual(asked, [('b.safetensors', 'https://h/b')])

    def test_markdown_lists_each_model_once(self):
        md = exporter.build_model_links_markdown([
            {'name': 'a.safetensors', 'url': 'https://h/a', 'directory': 'loras'},
            {'name': 'a.safetensors', 'url': 'https://h/a', 'directory': 'loras'},
            {'name': 'b.safetensors', 'url': 'https://h/b', 'directory': 'checkpoints'},
        ])
        self.assertIn('## Model links', md)
        self.assertEqual(md.count('[a.safetensors]'), 1)
        self.assertIn('[b.safetensors](https://h/b)', md)


class HashJobTests(LibraryTestCase):
    def test_hash_then_look_up(self):
        base = self.add_models('loras', ['h.safetensors'], ['.safetensors'])
        path = os.path.join(base, 'h.safetensors')
        expected = hashlib.sha256(open(path, 'rb').read()).hexdigest()
        job = exporter.start_hash_job(path, lambda sha, name: {'url': 'https://civitai.com/x?token=t', 'source': 'civitai'})
        deadline = time.time() + 5
        while exporter.get_hash_job(job)['status'] not in ('done', 'error') and time.time() < deadline:
            time.sleep(0.02)
        result = exporter.get_hash_job(job)
        self.assertEqual(result['sha256'], expected)
        self.assertEqual(result['result']['url'], 'https://civitai.com/x')
```

Add to `SummaryTests` in `tests/test_inventory.py`:

```python
    def test_the_workflow_link_is_kept_for_export(self):
        # Export reuses the author's link rather than searching for another
        refs = [{'original_path': 'x.safetensors', 'exists': True, 'category': 'loras',
                 'source_url': 'https://h/x', 'source_hash': 'ab' * 32, 'source_hash_type': 'sha256'}]
        entry = summarize_present_models(refs)[0]
        self.assertEqual(entry['source_url'], 'https://h/x')
        self.assertEqual(entry['source_hash'], 'ab' * 32)
        self.assertEqual(entry['source_hash_type'], 'sha256')
```

Create `tests/test_node.py`:

```python
"""The Model Linker node marks shared workflows and never executes."""

import importlib.util
import os
import unittest

EXTENSION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NodeTests(unittest.TestCase):
    def test_node_has_no_inputs_or_outputs(self):
        spec = importlib.util.spec_from_file_location(
            'model_linker_under_test', os.path.join(EXTENSION_DIR, '__init__.py'),
            submodule_search_locations=[EXTENSION_DIR])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # route setup fails quietly without ComfyUI's server
        node = module.NODE_CLASS_MAPPINGS['ModelLinker']
        self.assertEqual(node.INPUT_TYPES(), {'required': {}})
        self.assertEqual(node.RETURN_TYPES, ())
        self.assertFalse(getattr(node, 'OUTPUT_NODE', False))
        self.assertEqual(module.NODE_DISPLAY_NAME_MAPPINGS['ModelLinker'], 'Model Linker')
```

(`test_node` works only while every relative import in `__init__.py` stays inside `setup_routes`, which returns at `from server import PromptServer` outside ComfyUI. Plans A and B keep it so.)

- [ ] **Step 3: Run to verify they fail**

Run: `python -m unittest discover -t . -s tests -p "test_exporter.py" -v`, then the same with `-p "test_inventory.py"` and `-p "test_node.py"`.
Expected: `test_exporter` passes, because Step 1 already edited the port — a surviving `fetch_size=False` fails `test_existing_link_wins_and_is_stripped`, and a missing `search` parameter fails `test_no_search_without_permission`. `test_the_workflow_link_is_kept_for_export` FAILS with `KeyError: 'source_url'`. `test_node` FAILS with `KeyError: 'ModelLinker'`.

- [ ] **Step 4: Add the node, and keep the workflow's link on present models**

In `__init__.py`, replace

```python
# Empty NODE_CLASS_MAPPINGS - we don't provide custom nodes, only web extension
# This prevents ComfyUI from showing "IMPORT FAILED" message
NODE_CLASS_MAPPINGS = {}

__all__ = ["WEB_DIRECTORY"]
```

with

```python
class ModelLinkerNode:
    """
    Marks a shared workflow as prepared with Model Linker and carries its
    buttons (added by web/linker.js). No inputs and no outputs, so ComfyUI
    never executes it. Export adds it muted, so a workflow carrying it still
    runs where this extension is not installed.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = ()
    FUNCTION = "noop"
    CATEGORY = "utils"

    def noop(self):
        return ()


NODE_CLASS_MAPPINGS = {"ModelLinker": ModelLinkerNode}
NODE_DISPLAY_NAME_MAPPINGS = {"ModelLinker": "Model Linker"}

__all__ = ["WEB_DIRECTORY", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
```

In `core/linker.py`, extend `_PRESENT_MODEL_FIELDS` (line ~276):

```python
_PRESENT_MODEL_FIELDS = (
    'node_id', 'node_type', 'widget_index', 'original_path', 'category',
    'nested_key', 'list_index', 'adapter_id',
    'subgraph_id', 'subgraph_name', 'is_top_level',
    # The workflow's own properties.models link and hash: Export keeps the
    # author's URL instead of searching for, and writing over it with, another
    'source_url', 'source_hash', 'source_hash_type',
)
```

- [ ] **Step 5: Add the export routes**

Create `core/routes_export.py`:

```python
"""Routes for the Export dialog. Registered with the download routes (needs `requests`)."""

import asyncio

from . import exporter
from .reveal import locate_model
from .settings import get_option
from .source_chain import find_by_hash, find_download_source

MAX_MODELS = 500
LOOKUP_FIELDS = ('filename', 'category', 'existing_url', 'sha256')


def _bad(web, message):
    return web.json_response({'error': message}, status=400)


async def _body(request):
    try:
        return await request.json()
    except Exception:
        return None


def register(routes, web) -> None:
    @routes.post("/model_linker/export/lookup")
    async def lookup(request):
        data = await _body(request)
        models = data.get('models') if isinstance(data, dict) else None
        if not isinstance(models, list) or len(models) > MAX_MODELS:
            return _bad(web, f'models must be a list of at most {MAX_MODELS}')
        # One entry per item, malformed ones included, so links[i] answers models[i]
        clean = [{k: m[k] for k in LOOKUP_FIELDS if isinstance(m.get(k), str)} if isinstance(m, dict) else {}
                 for m in models]
        # A press of "Find links" is the user asking; otherwise the auto_lookup
        # switch decides whether filenames go to HuggingFace and CivitAI. A model
        # that already has a link is only stripped of credentials either way.
        searched = data.get('manual') is True or bool(get_option('auto_lookup'))
        links = await asyncio.to_thread(exporter.lookup_links, clean, find_download_source, searched)
        return web.json_response({'links': links, 'searched': searched})

    @routes.post("/model_linker/export/markdown")
    async def markdown(request):
        data = await _body(request)
        models = data.get('models') if isinstance(data, dict) else None
        if not isinstance(models, list) or len(models) > MAX_MODELS:
            return _bad(web, 'models must be a list')
        clean = [{'name': m.get('name', ''), 'url': exporter.strip_secrets(m.get('url', '')),
                  'directory': m.get('directory', '')}
                 for m in models if isinstance(m, dict) and isinstance(m.get('url'), str)]
        return web.json_response({'markdown': exporter.build_model_links_markdown(clean)})

    @routes.post("/model_linker/export/hash")
    async def start_hash(request):
        data = await _body(request)
        if not isinstance(data, dict):
            return _bad(web, 'Body must be an object')
        path, reason = locate_model(data.get('category') or '', data.get('filename') or '')
        if not path:
            return _bad(web, reason or 'no such model')
        return web.json_response({'job_id': exporter.start_hash_job(path, find_by_hash)})

    @routes.get("/model_linker/export/hash/{job_id}")
    async def hash_status(request):
        job = exporter.get_hash_job(request.match_info['job_id'])
        return web.json_response(job) if job else web.json_response({'error': 'Unknown job'}, status=404)
```

In `__init__.py`'s guarded block (Plan B Task 3), after `routes_downloads.register(routes, web)`, add:

```python
                from .core import routes_export
                routes_export.register(routes, web)
```

- [ ] **Step 6: Run the Python tests**

Run: `python tests/run.py --python` — expected: all pass.

- [ ] **Step 7: Write the failing frontend test**

Create `tests/js/test_export.mjs`:

```js
// Export rows: one per file, the workflow's own link first; every link is
// stripped by the server before it is written; applying is one undo step that
// reuses what an earlier export added; the analysis is always fresh.
import { createChecker, loadModule, readSource } from './harness.mjs';

export const name = 'export';

export default async function run() {
  const check = createChecker(name);
  const { exportRows, lookupPayload } = await loadModule('modules/export-dialog.js');
  const rows = exportRows({
    missing_models: [
      { node_id: 1, widget_index: 0, original_path: 'a.safetensors', category: 'loras', source_url: 'https://civitai.com/a?token=t' },
      { node_id: 2, widget_index: 0, original_path: 'a.safetensors', category: 'loras' },
    ],
    present_models: [
      { node_id: 3, widget_index: 0, original_path: 'b.safetensors', category: 'checkpoints', canonical_category: 'checkpoints',
        source_url: 'https://h/b', source_hash: 'ab'.repeat(32), source_hash_type: 'sha256' },
      { node_id: 4, widget_index: 0, original_path: 'c.safetensors', category: 'vae', canonical_category: 'vae',
        source_hash: 'cd'.repeat(32), source_hash_type: 'blake3' },
    ],
  });
  check('one row per file', rows.length === 3);
  const a = rows.find(r => r.name === 'a.safetensors');
  check('every reference is kept', a.refs.length === 2);
  check('the workflow\'s link is used', a.url === 'https://civitai.com/a?token=t');
  const b = rows.find(r => r.name === 'b.safetensors');
  check('a present model keeps the workflow\'s link', b.present === true && b.url === 'https://h/b');
  check('a sha256 hash is used', b.sha256 === 'ab'.repeat(32));
  check('another hash type is not', rows.find(r => r.name === 'c.safetensors').sha256 === '');

  // Every row goes to the server, its link included, so links the workflow
  // carries or the user pasted come back stripped of tokens
  const payload = lookupPayload(rows);
  check('every row is sent', payload.length === rows.length);
  check('a row\'s link is sent for stripping',
        payload.find(p => p.filename === 'a.safetensors').existing_url === 'https://civitai.com/a?token=t');
  check('a row without a link sends none', payload.find(p => p.filename === 'c.safetensors').existing_url === undefined);

  const src = readSource('modules/export-dialog.js');
  const apply = src.slice(src.indexOf('async apply()'));
  const lookupAt = apply.indexOf('/model_linker/export/lookup');
  check('apply strips every link through the server before writing',
        lookupAt !== -1 && lookupAt < apply.indexOf('properties.models'));
  check('applying is one undo step',
        /emitBeforeChange\?\.\(\)[\s\S]*emitAfterChange\?\.\(\)/.test(src) && !/graph\.beforeChange/.test(src));
  check('an existing note is reused', /model_linker_note/.test(src));
  check('an existing Model Linker node is reused', /ModelLinker/.test(src) && /\.find\(/.test(src));
  check('the Model Linker node is muted, so the workflow runs without it', /marker\.mode = 2;/.test(src));
  check('links are written to the loader nodes', /properties\.models/.test(src) && /liveNodesFor\(/.test(src));
  check('names and urls are escaped', /escapeHtml\(row\.name\)/.test(src) && /escapeHtml\(row\.url/.test(src));
  check('lookups can be asked for by hand', /lookUp\(\{ manual: true \}\)/.test(src));

  const dialog = readSource('modules/linker-dialog.js');
  check('export analyses the workflow on screen', /const data = await this\.loadWorkflowData\(\);/.test(dialog));
  check('the analysis is returned to the caller',
        /this\.displayMissingModels\(this\.contentElement, data\);\s*return data;/.test(dialog));
  // (autoResolve100Percent has a local `analyzeData`; only the field is banned)
  check('no earlier analysis is kept around', !/this\.analyzeData/.test(dialog));
  return check.failures;
}
```

- [ ] **Step 8: Run to verify it fails**

Run: `node tests/js/run.mjs` — expected: FAIL (`modules/export-dialog.js` missing).

- [ ] **Step 9: Implement the export dialog**

Create `web/modules/export-dialog.js`:

```js
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
```

In `web/modules/linker-dialog.js`:
1. `import { ExportDialog } from "./export-dialog.js";`
2. Make `loadWorkflowData` return the analysis it rendered: directly after `this.displayMissingModels(this.contentElement, data);` add `return data;`, change the method's four bare `return;` statements (no content element, no workflow loaded, the stale-answer check, and the superseded `AbortError` in the catch) to `return null;`, and add `return null;` as its last statement (the failed-request path). Existing callers ignore the result.
3. Add a method:

```js
    async openExport() {
        // Always analyse the workflow on screen: apply() writes by node id, so an
        // earlier analysis - perhaps of another tab - must never be used
        const data = await this.loadWorkflowData();
        if (!data) return;   // superseded, no workflow, or failed; the dialog says which
        if (!this.exportDialog) this.exportDialog = new ExportDialog(this);
        this.exportDialog.show(data);
    }
```

4. In `createHeader`, add a third header button (same style as Overrides) with `id: "model-linker-export-btn"`, `textContent: "Export…"`, `title: "Add download links for sharing this workflow"`, `onclick: () => this.openExport()`.

In `web/linker.js`, add to `ModelLinker`:

```js
    openExport() {
        if (!this.dialog) this.dialog = new LinkerManagerDialog();
        // show() starts an analysis; openExport() starts a newer one, which
        // supersedes it and is the one the Export dialog is built from
        this.dialog.show();
        this.dialog.openExport();
    }
```

and to the extension config:

```js
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
```

- [ ] **Step 10: Run the whole suite**

Run: `python tests/run.py` — expected: all pass.

- [ ] **Step 11: Verify in the app**

Restart ComfyUI. Load a workflow using one present checkpoint and one LoRA.
1. Export… → links appear (or "No public link found"); "Find by hash" on the checkpoint reports progress then a result.
2. Paste a URL with `?token=abc` into a row, Apply with all options: a black "Model links" note at the left, a greyed (muted) Model Linker node above it with two buttons, and the loader nodes' `properties.models` (Properties panel) hold the links — the pasted one without `token`, in the note and in the properties.
3. Apply again: still one note, one Model Linker node. One Ctrl+Z undoes the whole export.
4. In the browser console, `(await app.graphToPrompt()).output` has no entry whose `class_type` is `ModelLinker`.
5. Switch to another workflow tab and press the Model Linker node's Export… there (or the header button): the rows describe that tab.
6. Turn background lookups off in ⚙ Settings, open Export… again: rows without a link say "Online lookups are off", the `/model_linker/export/lookup` response in the browser's Network tab has `"searched": false`, and "🔍 Find links" appears; pressing it sends `manual: true` and searches.

- [ ] **Step 12: Commit**

```bash
git add core/exporter.py core/routes_export.py core/linker.py __init__.py web/modules/export-dialog.js web/modules/linker-dialog.js web/linker.js tests/test_exporter.py tests/test_inventory.py tests/test_node.py tests/js/test_export.mjs
git commit -m "Export workflows with download links, and add the Model Linker node"
```

---

### Task 5: Bring the documentation and memory up to date

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `tests/README.md`
- Modify (outside the repo): `C:\Users\gontz\.claude\projects\C--ComfyUI-custom-nodes-comfyui-model-linker\memory\repo-provenance.md`, `...\memory\matcher-improvement-backlog.md`, `...\memory\upstream-parity-plans.md`, `...\memory\MEMORY.md`

**Interfaces:** none.

The lists below are what Plans A–C create as written on 2026-10-16. Plan B was amended in parallel; before editing, re-read the **Files:** sections of all three plans (`grep -n "^- Create\|^- Test" docs/superpowers/plans/2026-10-14-*.md`) and `git diff --stat main`, and add anything shipped that is missing here.

- [ ] **Step 1: CLAUDE.md**

1. In "Project Overview" (line 9), replace the whole paragraph "This is **not** a custom node — it provides no `NODE_CLASS_MAPPINGS`. It registers API routes on `PromptServer` and loads a web UI via `WEB_DIRECTORY`." with: "It registers one node, **Model Linker**, which has no inputs or outputs and never executes — it marks shared workflows and carries buttons, and Export adds it muted so the workflow still runs where this extension is not installed. The rest is API routes on `PromptServer` and a web UI via `WEB_DIRECTORY`."
2. In "This fork does not track upstream" (lines 13–17), make the text describe the current state: replace "The `upstream` remote has been removed deliberately." with "An `upstream` remote exists locally, fetch-only — its push URL is `DISABLED` — and is used only as the object source for `git show bec5c80:<path>` ports."; and replace the bullet's lead "**Do not add an `upstream` remote, and do not merge or rebase from kianxyzw.**" with "**Never merge or rebase from kianxyzw, and never re-enable pushing to `upstream`.**" (keep the rest of that bullet — the reason).
3. Plan B Task 0 already replaced the "no download feature" bullet and kept the removal story as history. Now add a section **"### Downloads and online lookups (reinstated 2026-10-14)"** describing the shipped design: how it answers both objections — lookups run in a background worker after `/analyze` answers, results persist in `data/lookups.json`, the `auto_lookup` setting turns them off (Export honours it too, except for an explicit "Find links" press), nothing contacts a workflow's own URL during lookup; similar-model search is user-initiated and a chosen file keeps its own name; the HuggingFace backfill of Lora Manager info is a server job started from ⚙ Settings (`data/lm-backfill.json`). Module list: `core/settings.py`, `core/routes_settings.py`, `core/sources/*` (`civitai`, `civarchive`, `huggingface`, `hf_index`, `popular`, `model_list`) with `metadata/*.json` and `tools/build_hf_index.py`, `source_chain.py`, `lookups.py`, `downloader.py`, `hf_downloader.py`/`_hf_worker.py`, `civitai_meta.py`, `lm_sidecar.py`, `online_search.py`, `lm_api.py`, `hf_identify.py`, `lm_backfill.py`, `exporter.py`, and `routes_downloads.py`/`routes_export.py`; and that everything except the settings store registers only when `requests` imports.
4. Add a section **"### Lora Manager sidecars"**: format contract pointer to the spec; GPL-3.0 vs MIT — implement from the documented format, copy no code; `file_path` uses the registered directory, not `realpath` (the one deliberate exception to "Physical File Identity"); never overwrite an existing sidecar.
5. Update the "Key Modules" table (including `core/overrides.py`'s `resolutions_to_record`, and that `__init__.py` now registers the Model Linker node) and the "API Routes" list with every new module and route from Plans A–C. As written they add: `/settings` (GET, POST), `/settings/check/{provider}`, `/lookups`, `/lookups/refresh`, `/download`, `/progress`, `/progress/{download_id}`, `/cancel/{download_id}`, `/search`, `/lm-backfill`, `/lm-backfill/start`, `/lm-backfill/cancel`, `/export/lookup`, `/export/markdown`, `/export/hash`, `/export/hash/{job_id}`. Note that `/resolve` saves no override for a resolution with `record_override: false` (Swap).
6. In "Frontend (`web/`)", add `settings-dialog.js`, `downloads.js`, `export-dialog.js` to the file table; change "Registers `commands`, `menuCommands`, `keybindings`, `settings` and `getCanvasMenuItems`." to include `afterConfigureGraph` and `nodeCreated`, and note that `afterConfigureGraph` replaces any popup injection; add that a graph mutation is grouped for undo with `app.canvas.emitBeforeChange()/emitAfterChange()`, not `graph.beforeChange()/afterChange()`.
7. In "Development Environment" (line 46), change "**Python >=3.8**, no external dependencies beyond ComfyUI's own (aiohttp, folder_paths, server)" to "**Python >=3.9**; `requests` (declared; downloads and lookups need it); optional `huggingface_hub` ≥1.1 for multi-connection HuggingFace downloads; optional `rapidfuzz`".

- [ ] **Step 2: README.md and tests/README.md**

README: add feature bullets for background lookups, downloads (two at a time, continue when the dialog closes), Lora Manager info, 🔎 Search similar (user-initiated; a chosen file keeps its own name), filling empty Lora Manager info from HuggingFace (⚙ Settings), Swap, Locate in subgraphs, Export (links stripped of tokens, a muted Model Linker node), the load-time notice; an "API tokens" paragraph (⚙ Settings, `HF_TOKEN` / `CIVITAI_API_TOKEN`); and a "Privacy" paragraph: what background lookups, Search similar, Export and the backfill send, and how to switch lookups off.

`tests/README.md`: add rows to the Layout table for every new suite — Python: `test_node_schema.py`, `test_settings.py`, `test_sources.py`, `test_source_chain.py`, `test_lookups.py`, `test_downloader.py`, `test_hf_routing.py`, `test_lm_sidecar.py`, `test_online_search.py`, `test_hf_identify.py`, `test_lm_backfill.py`, `test_exporter.py`, `test_node.py`; frontend: `js/test_perfect_match.mjs`, `js/test_settings_dialog.mjs`, `js/test_downloads.mjs`, `js/test_locate.mjs`, `js/test_swap.mjs`, `js/test_export.mjs` — and mention what was added to existing ones (`test_matcher.py`, `test_scanner.py`, `test_overrides.py`, `test_inventory.py`, `js/test_escaping.mjs`, `js/test_extension.mjs`). Note that suites needing `requests` skip without it, and that `test_lm_sidecar.py` validates against Lora Manager's published schema when it is installed alongside.

- [ ] **Step 3: Memory**

- `repo-provenance.md`: the body already records the reinstatement and the push-disabled `upstream` remote. Change its frontmatter `description` from "…Never re-add the upstream remote." to "The fork deliberately no longer tracks upstream kianxyzw; main is gontz's own line. A fetch-only upstream remote (push DISABLED) exists only for `git show bec5c80:` ports; never merge or rebase from it." and update `modified`.
- `upstream-parity-plans.md`: mark the plans implemented — add "**Implemented** <date> on `feature/upstream-parity`: Plans A–C complete", plus any deviation from the plans, and say so in its `description`.
- `matcher-improvement-backlog.md`: under the backlog, record "Word-order similarity (Plan A Task 1) done; download ecosystem moved from out-of-scope to done (Plans B–C)."
- `MEMORY.md`: update the hooks of the `repo-provenance`, `upstream-parity-plans` and `matcher-improvement-backlog` lines to match.

- [ ] **Step 4: Run the whole suite and commit**

Run: `python tests/run.py` — expected: all pass.

```bash
git add CLAUDE.md README.md tests/README.md
git commit -m "Document downloads, lookups, Lora Manager sidecars and the new UI"
```
