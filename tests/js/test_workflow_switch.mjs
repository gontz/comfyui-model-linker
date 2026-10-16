// A queue of selections belongs to the workflow it was built on, and what
// needs `requests` is hidden when the server has no online lookups.
// Behavioural, against the real ModelLinker and LinkerManagerDialog methods.
import { createChecker, loadModule, readSource } from './harness.mjs';
import { app } from './stubs/scripts/app.js';
import { api } from './stubs/scripts/api.js';

export const name = 'workflow switch';

export default async function run() {
  const check = createChecker(name);
  const { LinkerManagerDialog } = await loadModule('modules/linker-dialog.js');
  const { ModelLinker } = await loadModule('linker.js');

  const hadDocument = 'document' in globalThis;
  const originalDocument = globalThis.document;
  const hadWorkflowStore = Object.prototype.hasOwnProperty.call(app.extensionManager, 'workflow');
  const originalWorkflowStore = app.extensionManager.workflow;
  const originalFetch = api.fetchApi;
  globalThis.document = { getElementById: () => null, querySelectorAll: () => [] };

  try {
    // --- M3: a workflow switch clears the queue -------------------------
    const dialog = Object.create(LinkerManagerDialog.prototype);
    dialog.pendingResolutions = [];
    dialog.pendingIndex = new Map();
    dialog.element = { style: { display: 'flex' } };   // open
    dialog.analyzeAbort = null;
    dialog.analyses = 0;
    dialog.loadWorkflowData = async function () { this.analyses++; return null; };
    const queueOne = () => dialog.queueResolution(
      { node_id: 1, widget_index: 0, original_path: 'a.safetensors', category: 'loras' },
      { path: '/m/loras/b.safetensors', filename: 'b.safetensors', relative_path: 'b.safetensors' });

    const linker = new ModelLinker();
    linker.dialog = dialog;
    // In the order frontend 1.55's loadGraphData uses: afterConfigureGraph
    // while the store still names the workflow being left, then the store
    // switches, then afterLoadGraph.
    const load = (path) => {
      linker.onGraphLoaded();
      clearTimeout(linker.loadTimer);   // the background check needs a real graph
      app.extensionManager.workflow = { activeWorkflow: { path } };
      linker.onWorkflowLoaded();
    };

    load('workflows/a.json');
    queueOne();
    check('a selection is queued on workflow A', dialog.pendingResolutions.length === 1);
    const before = dialog.analyses;
    load('workflows/a.json');   // the same workflow again (an undo reloads it)
    check('reloading the same workflow keeps the queue', dialog.pendingResolutions.length === 1);
    check('...and does not re-analyse', dialog.analyses === before);

    load('workflows/b.json');
    check('switching to workflow B clears the queue',
          dialog.pendingResolutions.length === 0 && dialog.pendingIndex.size === 0);
    check('an open dialog re-analyses for workflow B', dialog.analyses === before + 1);

    queueOne();
    dialog.element.style.display = 'none';   // closed
    load('workflows/c.json');
    check('a closed dialog\'s queue is cleared too', dialog.pendingResolutions.length === 0);
    check('a closed dialog is not re-analysed', dialog.analyses === before + 1);

    dialog.element.style.display = 'flex';
    queueOne();
    delete app.extensionManager.workflow;   // a frontend with no workflow store
    linker.onWorkflowLoaded();
    check('with no workflow identity any load clears the queue', dialog.pendingResolutions.length === 0);

    const fresh = new ModelLinker();   // the first graph can load before the dialog exists
    app.extensionManager.workflow = { activeWorkflow: { path: 'workflows/a.json' } };
    let threw = false;
    try { fresh.onGraphLoaded(); fresh.onWorkflowLoaded(); } catch (e) { threw = true; }
    clearTimeout(fresh.loadTimer);
    check('a load before the dialog exists does not throw', !threw);

    // (registerExtension already ran when another suite imported linker.js)
    check('the switch check runs on afterLoadGraph, once the store names the new workflow',
          /afterLoadGraph:\s*\(\)\s*=>\s*modelLinker\.onWorkflowLoaded\(\)/.test(readSource('linker.js')));

    // --- M2: without online lookups, hide what needs them ---------------
    const shown = Object.create(LinkerManagerDialog.prototype);
    shown.contentElement = { innerHTML: '' };
    shown.downloads = { reset() {} };
    shown.downloadAllBtn = { style: { display: '' } };
    shown.getCurrentWorkflow = () => ({ nodes: [] });
    shown.displayMissingModels = () => {};
    const answer = (body) => { api.fetchApi = async () => ({ ok: true, json: async () => body }); };

    answer({ missing_models: [], lookups_available: false });
    await shown.loadWorkflowData();
    check('"Download all found" is hidden when lookups are unavailable',
          shown.downloadAllBtn.style.display === 'none');
    answer({ missing_models: [], lookups_available: true });
    await shown.loadWorkflowData();
    check('...and shown again when they are', shown.downloadAllBtn.style.display === '');

    const exporter = Object.create(LinkerManagerDialog.prototype);
    const notes = [];
    exporter.showNotification = (message, kind) => notes.push({ message, kind });
    exporter.loadWorkflowData = async () => ({ missing_models: [], present_models: [], lookups_available: false });
    let opened = false;
    try { await exporter.openExport(); } catch (e) { opened = true; }   // building ExportDialog needs a DOM
    check('Export is not opened without online lookups', !opened && !exporter.exportDialog);
    check('...and says it needs requests', notes.length === 1 && /requests/.test(notes[0].message)
          && notes[0].kind === 'error');
  } finally {
    api.fetchApi = originalFetch;
    if (hadWorkflowStore) app.extensionManager.workflow = originalWorkflowStore;
    else delete app.extensionManager.workflow;
    if (hadDocument) globalThis.document = originalDocument;
    else delete globalThis.document;
  }
  return check.failures;
}
