// Export rows: one per file, the workflow's own link first; every link is
// stripped by the server before it is written; applying is one undo step that
// reuses what an earlier export added; the analysis is always fresh.
//
// apply()'s own contract - idempotent re-apply, re-muting, credential scrub
// even when nothing is being added - used to be pinned with source-text
// regexes that never actually ran the method. None of them would have caught
// a stale token surviving in properties.models, so this file drives the real
// ExportDialog instead: Object.create(ExportDialog.prototype) skips the
// DOM-building constructor, and app.graph/app.canvas/globalThis.LiteGraph are
// faked just enough for apply() to run against.
import { createChecker, loadModule, readSource } from './harness.mjs';
import { app } from './stubs/scripts/app.js';

export const name = 'export';

function makeGraph() {
  const nodes = [];
  return {
    _nodes: nodes,
    add(node) { node.graph = this; nodes.push(node); },
    setDirtyCanvas() {},
  };
}

function makeLiteGraph() {
  return {
    createNode(type) {
      return { type, pos: [0, 0], size: [0, 0], properties: {}, widgets: [{ value: '' }], widgets_values: [], mode: 0, graph: null };
    },
  };
}

/** Swap in fakes for app.graph / app.canvas / globalThis.LiteGraph, restoring them after `fn`. */
async function withFakeCanvas(fn) {
  const savedGraph = app.graph;
  const savedCanvas = app.canvas;
  const hadLG = Object.prototype.hasOwnProperty.call(globalThis, 'LiteGraph');
  const savedLG = globalThis.LiteGraph;
  const canvasCalls = [];
  const graph = makeGraph();
  app.graph = graph;
  app.canvas = { emitBeforeChange: () => canvasCalls.push('before'), emitAfterChange: () => canvasCalls.push('after') };
  globalThis.LiteGraph = makeLiteGraph();
  try {
    return await fn({ graph, canvasCalls });
  } finally {
    app.graph = savedGraph;
    app.canvas = savedCanvas;
    if (hadLG) globalThis.LiteGraph = savedLG; else delete globalThis.LiteGraph;
  }
}

export default async function run() {
  const check = createChecker(name);
  const { exportRows, lookupPayload, ExportDialog } = await loadModule('modules/export-dialog.js');

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

  // --- apply(), driven for real -------------------------------------------
  //
  // Scenario 1: two models with a credentialed link each (one pasted with
  // userinfo and a fragment, one from the workflow), every option on. apply()
  // twice - the second call must not duplicate the note or the marker, must
  // re-mute an already-muted marker, and nothing anywhere in what got written
  // may still carry a credential: the dialog must have used the *server's*
  // stripped answer, never the raw pasted/workflow URL.
  await withFakeCanvas(async ({ graph, canvasCalls }) => {
    const refA = { node_id: 1, widget_index: 0, original_path: 'a.safetensors', category: 'loras' };
    const refB = { node_id: 2, widget_index: 0, original_path: 'b.safetensors', category: 'checkpoints' };
    const nodeA = { properties: {} };
    const nodeB = { properties: {} };
    const linker = { liveNodesFor: (ref) => (ref === refA ? [nodeA] : ref === refB ? [nodeB] : []) };
    const rowA = { key: 'loras|a.safetensors', name: 'a.safetensors', category: 'loras', original_path: 'a.safetensors',
      url: 'https://user:pw@host/a?token=NEW#token=FRAG', sha256: '', present: false, refs: [refA] };
    const rowB = { key: 'checkpoints|b.safetensors', name: 'b.safetensors', category: 'checkpoints', original_path: 'b.safetensors',
      url: 'https://host/b?token=XYZ', sha256: '', present: true, refs: [refB] };
    const STRIPPED = { 'a.safetensors': 'https://host/a', 'b.safetensors': 'https://host/b' };
    const requestCalls = [];
    const dialog = Object.create(ExportDialog.prototype);
    dialog.rows = [rowA, rowB];
    dialog.linker = linker;
    dialog.option = () => true;   // note, properties and node all on
    dialog.close = () => { dialog.closed = (dialog.closed || 0) + 1; };
    dialog.request = async (path, body) => {
      requestCalls.push(path);
      if (path === '/model_linker/export/lookup') {
        return { links: body.models.map((m) => ({ url: STRIPPED[m.filename], source: 'workflow', url_source: 'workflow' })), searched: false };
      }
      if (path === '/model_linker/export/markdown') {
        return { markdown: '## Model links\n\n' + body.models.map((m) => `- [${m.name}](${m.url})`).join('\n') };
      }
      throw new Error('unexpected request: ' + path);
    };

    await dialog.apply();
    await dialog.apply();   // idempotent re-apply, same rows and options

    const notes = graph._nodes.filter((n) => n.properties?.model_linker_note);
    const markers = graph._nodes.filter((n) => n.type === 'ModelLinker');
    check('re-applying leaves exactly one note', notes.length === 1);
    check('re-applying leaves exactly one marker', markers.length === 1);
    check('the marker is muted', markers[0]?.mode === 2);
    check('two applies make two before/after pairs', canvasCalls.join(',') === 'before,after,before,after');
    check('the lookup ran before anything was written to the loader nodes',
          requestCalls[0] === '/model_linker/export/lookup');

    const noteText = String(notes[0]?.widgets_values?.[0] || '');
    const propertyUrls = [nodeA, nodeB].flatMap((n) => (n.properties.models || []).map((m) => m.url || ''));
    const leaked = (text) => /token=/i.test(text) || /:\/\/[^/]*@/.test(text) || /#\S*token/i.test(text);
    check('the note carries no credential', !leaked(noteText));
    check('no written property url carries a credential', !propertyUrls.some(leaked));
    check('both rows got the server\'s clean link, not the raw pasted/workflow one',
          propertyUrls.includes('https://host/a') && propertyUrls.includes('https://host/b'));
  });

  // Scenario 2 (binding focus item 2 / item 3c): a model whose entry in a
  // loader node's properties.models already carries a token, the row's own
  // url cleared by the user, and every Export option OFF. Nothing may be
  // added (the options are off), but the stale credential already sitting on
  // the node must still be removed - scrubbing an existing entry is not
  // gated by "write links into the loader nodes".
  await withFakeCanvas(async ({ graph }) => {
    const refX = { node_id: 9, widget_index: 0, original_path: 'x.safetensors', category: 'vae' };
    const nodeX = { properties: { models: [{ name: 'x.safetensors', url: 'https://host/x?token=OLD', directory: 'vae' }] } };
    const linker = { liveNodesFor: (ref) => (ref === refX ? [nodeX] : []) };
    const rowX = { key: 'vae|x.safetensors', name: 'x.safetensors', category: 'vae', original_path: 'x.safetensors',
      url: '', sha256: '', present: true, refs: [refX] };   // cleared - no candidate link to resolve
    const dialog = Object.create(ExportDialog.prototype);
    dialog.rows = [rowX];
    dialog.linker = linker;
    dialog.option = () => false;   // note, properties, node all unchecked
    dialog.close = () => {};
    dialog.request = async (path) => { throw new Error('nothing should be sent for a row with no link: ' + path); };

    await dialog.apply();

    check('a stale credential is scrubbed even with "write links" unchecked',
          !('url' in nodeX.properties.models[0]));
    check('nothing is added while every option is off', graph._nodes.length === 0);
  });

  // --- source-level checks for what is impractical to drive end-to-end ----
  const src = readSource('modules/export-dialog.js');
  check('applying ignores a double press', /this\._applying/.test(src));
  check('finding by hash ignores a double press on the same row', /this\._hashBusy/.test(src));
  check('findByHash bails once the row it was called for is stale', (src.match(/this\.rows\[i\] !== row/g) || []).length >= 3);
  check('an existing Model Linker node is re-muted on re-apply', /existingMarker\.mode = 2;/.test(src));
  check('a re-apply with no links empties a stale note', /existingNote\.widgets_values = \[""\];/.test(src));
  check('names and urls are escaped', /escapeHtml\(row\.name\)/.test(src) && /escapeHtml\(row\.url/.test(src));
  check('lookups can be asked for by hand', /lookUp\(\{ manual: true \}\)/.test(src));

  const linkerSrc = readSource('linker.js');
  check('the export dialog is closed when another workflow loads',
        /onGraphLoaded\(\)\s*\{\s*this\.dialog\?\.exportDialog\?\.close\(\);/.test(linkerSrc));
  check('opening Export reports a failure instead of throwing', /notifyError\("Could not open Export"/.test(linkerSrc));

  const dialog = readSource('modules/linker-dialog.js');
  check('export analyses the workflow on screen', /const data = await this\.loadWorkflowData\(\);/.test(dialog));
  check('the analysis is returned to the caller',
        /this\.displayMissingModels\(this\.contentElement, data\);\s*return data;/.test(dialog));
  // (autoResolve100Percent has a local `analyzeData`; only the field is banned)
  check('no earlier analysis is kept around', !/this\.analyzeData/.test(dialog));
  return check.failures;
}
