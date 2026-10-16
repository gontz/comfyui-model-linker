// A swap touches a whole group of references (every node loading one model),
// and must stay a group everywhere it can be removed from, survive a
// re-render, and never confuse a subgraph-internal node with a colliding
// top-level id. Behavioural, against the real dialog methods, rather than
// grepping the source for their shape.
import { createChecker, loadModule, readSource } from './harness.mjs';
import { app } from './stubs/scripts/app.js';

export const name = 'swap';

const ref = (id, path, category = 'loras', extra = {}) =>
  ({ node_id: id, widget_index: 0, original_path: path, category, canonical_category: category, ...extra });

export default async function run() {
  const check = createChecker(name);
  const { groupPresentModels, refKey, refSlot } = await loadModule('modules/util.js');
  const { LinkerManagerDialog } = await loadModule('modules/linker-dialog.js');

  // `updateSelectedBarForMissing` (called by queueResolution/removeQueuedResolution)
  // reads the DOM by id; none of these tests render real markup, so a lookup
  // that finds nothing is exactly what should happen.
  globalThis.document = { getElementById: () => null, querySelectorAll: () => [] };

  // --- grouping -------------------------------------------------------
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

  const separatorGroups = groupPresentModels([ref(6, 'sub\\a.safetensors'), ref(7, 'sub/a.safetensors')]);
  check('a backslash and a forward-slash path group as one row',
        separatorGroups.length === 1 && separatorGroups[0].refs.length === 2);

  // --- queueSwap / removeSwapGroup (extracted from the picker's onChoose) --
  const group = {
    key: 'loras|a.safetensors', name: 'a.safetensors', category: 'loras',
    refs: [1, 2, 3, 4].map((id) => ref(id, 'a.safetensors')),
  };
  const model = { path: '/models/loras/new.safetensors', filename: 'new.safetensors', relative_path: 'new.safetensors' };

  const dialog = Object.create(LinkerManagerDialog.prototype);
  dialog.pendingResolutions = [];
  dialog.pendingIndex = new Map();
  dialog.queueSwap(group, model);
  check('a 4-ref group queues 4 entries', dialog.pendingResolutions.length === 4);
  check('every queued swap entry is marked not to be recorded as an override',
        dialog.pendingResolutions.every((r) => r.record_override === false));

  // An unrelated resolution on one of the same nodes (a different widget)
  // must survive a swap's group-wide remove.
  dialog.queueResolution(
    { node_id: 1, widget_index: 9, original_path: 'unrelated.safetensors', category: 'loras' },
    { path: '/models/loras/unrelated-2.safetensors', filename: 'unrelated-2.safetensors' });
  check('setup: 5 queued before the group remove', dialog.pendingResolutions.length === 5);
  dialog.removeSwapGroup(group);
  check('group Remove drops all 4 swap entries', dialog.pendingResolutions.length === 1);
  check('an unrelated queued entry on the same node is left alone',
        dialog.pendingResolutions[0].original_path === 'unrelated.safetensors');

  // --- removeQueuedByIndex treats one swap row as its whole group ---------
  const dialog2 = Object.create(LinkerManagerDialog.prototype);
  dialog2.pendingResolutions = [];
  dialog2.pendingIndex = new Map();
  dialog2.queueSwap(group, model);
  dialog2.queueResolution(
    { node_id: 1, widget_index: 9, original_path: 'unrelated.safetensors', category: 'loras' },
    { path: '/models/loras/unrelated-2.safetensors' });
  const swapRowIndex = dialog2.pendingResolutions.findIndex((r) => r.record_override === false);
  dialog2.removeQueuedByIndex(swapRowIndex);
  check('removing one swap row from the queue panel drops the whole group',
        dialog2.pendingResolutions.length === 1
        && dialog2.pendingResolutions[0].original_path === 'unrelated.safetensors');

  // A non-swap row removed by index still removes only itself.
  const dialog2b = Object.create(LinkerManagerDialog.prototype);
  dialog2b.pendingResolutions = [];
  dialog2b.pendingIndex = new Map();
  dialog2b.queueResolution({ node_id: 1, widget_index: 0, original_path: 'c.safetensors', category: 'loras' }, model);
  dialog2b.queueResolution({ node_id: 2, widget_index: 0, original_path: 'd.safetensors', category: 'loras' }, model);
  dialog2b.removeQueuedByIndex(0);
  check('removing a non-swap row by index removes only that one',
        dialog2b.pendingResolutions.length === 1
        && dialog2b.pendingResolutions[0].original_path === 'd.safetensors');

  // --- removeQueuedByIndex passes list_index through ----------------------
  const dialog3 = Object.create(LinkerManagerDialog.prototype);
  dialog3.pendingResolutions = [];
  dialog3.pendingIndex = new Map();
  dialog3.queueResolution(ref(5, 'x.safetensors', 'loras', { list_index: 1 }), model);
  const seenSlots = [];
  dialog3.updateSelectedBarForMissing = (m) => seenSlots.push(m.list_index);
  dialog3.removeQueuedByIndex(0);
  check('removeQueuedByIndex rebuilds the reference with its list_index, so the right bar is found',
        seenSlots.length === 1 && seenSlots[0] === 1);

  // --- a queued swap survives a re-render ----------------------------------
  const fakeEl = () => {
    const subs = new Map();
    return {
      style: {}, dataset: {}, onclick: null,
      querySelector(sel) {
        const id = sel.slice(1);
        if (!subs.has(id)) subs.set(id, fakeEl());
        return subs.get(id);
      },
    };
  };
  const fakeContainer = () => {
    const els = new Map();
    return {
      querySelector(sel) {
        const id = sel.slice(1);
        if (!els.has(id)) els.set(id, fakeEl());
        return els.get(id);
      },
    };
  };

  const dialog4 = Object.create(LinkerManagerDialog.prototype);
  const restored = [];
  dialog4.updateSelectedBarForMissing = (m) => restored.push(m);
  dialog4.ensureAllModelsLoaded = async () => {};
  dialog4.attachModelCombo = () => {};
  dialog4.revealModel = () => {};
  dialog4.removeQueuedResolution = () => {};
  dialog4._presentGroups = [group];
  // Simulate: a swap for this group was queued before this render rebuilt
  // the row from scratch (refKey must match exactly what queueResolution
  // would have stored it under).
  dialog4.pendingIndex = new Map([[refKey(group.refs[0]), 0]]);
  const container4 = fakeContainer();
  dialog4.attachPresentModelHandlers(container4);
  const panel4 = container4.querySelector('#present-swap-panel-0');
  check('a queued swap restores its bar on re-render', restored.length === 1);
  check('a queued swap reopens its panel on re-render', panel4.style.display === '');
  const remove4 = panel4.querySelector(`#selected-remove-${refSlot(group.refs[0])}`);
  check('its Remove is rebound to the whole group', typeof remove4.onclick === 'function');

  // A group with nothing queued is left collapsed and untouched.
  const dialog4b = Object.create(LinkerManagerDialog.prototype);
  const restoredNone = [];
  dialog4b.updateSelectedBarForMissing = (m) => restoredNone.push(m);
  dialog4b.ensureAllModelsLoaded = async () => {};
  dialog4b.attachModelCombo = () => {};
  dialog4b.revealModel = () => {};
  dialog4b.pendingIndex = new Map();
  dialog4b._presentGroups = [group];
  const container4b = fakeContainer();
  dialog4b.attachPresentModelHandlers(container4b);
  check('a group with nothing queued is not restored',
        restoredNone.length === 0
        && container4b.querySelector('#present-swap-panel-0').style.display !== '');

  // --- updateWorkflowInComfyUI honours is_top_level for colliding ids -----
  const dialog5 = Object.create(LinkerManagerDialog.prototype);
  const subWidget = { value: 'old' };
  const rootWidget = { value: 'old' };
  dialog5.findLiveNodeInSubgraphs = () => ({ widgets: [subWidget] });
  const savedGraph = app.graph;
  const savedCanvas = app.canvas;
  app.graph = { getNodeById: (id) => (id === 5 ? { widgets: [rootWidget] } : null), setDirtyCanvas: () => {} };
  const workflow = {
    nodes: [{ id: 5, widgets_values: ['root-value'] }],
    definitions: { subgraphs: [{ id: 'sg1', nodes: [{ id: 5, widgets_values: ['sub-value'] }] }] },
  };
  await dialog5.updateWorkflowInComfyUI(workflow, [
    { node_id: 5, widget_index: 0, subgraph_id: 'sg1', is_top_level: false },
    { node_id: 5, widget_index: 0 },
  ]);
  check('a subgraph-internal node is read from its own definition, not a colliding root node',
        subWidget.value === 'sub-value');
  check('a true top-level resolution with the same id still reads the root node',
        rootWidget.value === 'root-value');
  app.graph = savedGraph;
  app.canvas = savedCanvas;

  const src = readSource('modules/linker-dialog.js');
  check('applying selections is one undo step',
        /updateWorkflowInComfyUI\(workflow, resolutions\) \{[\s\S]{0,1200}emitBeforeChange\?\.\(\)[\s\S]*?emitAfterChange\?\.\(\)/.test(src));

  return check.failures;
}
