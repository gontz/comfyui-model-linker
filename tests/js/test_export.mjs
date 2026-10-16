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
  const separatorRows = exportRows({
    missing_models: [{ node_id: 5, widget_index: 0, original_path: 'sub\\a.safetensors', category: 'loras' }],
    present_models: [{ node_id: 6, widget_index: 0, original_path: 'sub/a.safetensors', category: 'loras' }],
  });
  check('a backslash and a forward-slash path are one row',
        separatorRows.length === 1 && separatorRows[0].refs.length === 2);

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

  // Scenario 3 (item 1): the analyzer attributes a properties.models entry to
  // a widget by its exact value OR by basename on both sides
  // (core/workflow_analyzer.py:85, :302-303). A widget holding "sub/x..."
  // with an entry filed only under the bare "x..." must be found the same
  // way here, both to scrub it and to avoid pushing a duplicate.
  await withFakeCanvas(async ({ graph }) => {
    // 3a: options off, the row's link cleared - the basename-matched entry's
    // credential must still be removed, and nothing duplicated.
    const refY = { node_id: 20, widget_index: 0, original_path: 'sub/x.safetensors', category: 'loras' };
    const nodeY = { properties: { models: [{ name: 'x.safetensors', url: 'https://h/x?token=OLD', directory: 'loras' }] } };
    const linkerY = { liveNodesFor: (ref) => (ref === refY ? [nodeY] : []) };
    const rowY = { key: 'loras|sub/x.safetensors', name: 'x.safetensors', category: 'loras', original_path: 'sub/x.safetensors',
      url: '', sha256: '', present: true, refs: [refY] };
    const dialogY = Object.create(ExportDialog.prototype);
    dialogY.rows = [rowY];
    dialogY.linker = linkerY;
    dialogY.option = () => false;
    dialogY.close = () => {};
    dialogY.request = async (path) => { throw new Error('nothing to resolve: ' + path); };
    await dialogY.apply();
    check('a basename-matched entry is scrubbed with options off',
          nodeY.properties.models.length === 1 && !('url' in nodeY.properties.models[0]));

    // 3b: options on, a new link resolved - the SAME basename-matched entry
    // is updated in place (its own name kept), not duplicated under the
    // widget's full (subfoldered) value.
    const refZ = { node_id: 21, widget_index: 0, original_path: 'sub/x.safetensors', category: 'loras' };
    const nodeZ = { properties: { models: [{ name: 'x.safetensors', url: 'https://h/x?token=OLD', directory: 'loras' }] } };
    const linkerZ = { liveNodesFor: (ref) => (ref === refZ ? [nodeZ] : []) };
    const rowZ = { key: 'loras|sub/x.safetensors', name: 'x.safetensors', category: 'loras', original_path: 'sub/x.safetensors',
      url: 'https://user:pw@host/newx?token=NEW', sha256: '', present: true, refs: [refZ] };
    const dialogZ = Object.create(ExportDialog.prototype);
    dialogZ.rows = [rowZ];
    dialogZ.linker = linkerZ;
    dialogZ.option = () => true;
    dialogZ.close = () => {};
    dialogZ.request = async (path, body) => {
      if (path === '/model_linker/export/lookup') {
        return { links: body.models.map(() => ({ url: 'https://host/newx', source: 'workflow', url_source: 'workflow' })) };
      }
      if (path === '/model_linker/export/markdown') return { markdown: '## Model links' };
      throw new Error('unexpected request: ' + path);
    };
    await dialogZ.apply();
    check('a basename-matched entry is updated in place, not duplicated',
          nodeZ.properties.models.length === 1);
    check('the matched entry keeps its own stored name', nodeZ.properties.models[0]?.name === 'x.safetensors');
    check('the matched entry\'s link is the server\'s clean one',
          nodeZ.properties.models[0]?.url === 'https://host/newx');
  });

  // Scenario 3c (item 1 core bug): two widgets on ONE node sharing a
  // basename - DualCLIPLoader's two t5.safetensors under different
  // subfolders - with an EXACT properties.models entry for only one of
  // them. The basename fallback must never land the other's change on the
  // entry a DIFFERENT ref on the same node already claims by its exact name.
  //
  // First with the rows exactly as the analyzer supplies them: since round 4
  // it gives the sd3 ref no source_url (core/workflow_analyzer.py skips an
  // entry another widget names exactly - test_analyzer.py pins that). Before,
  // it handed sd3 flux's link, and Export pushed a NEW sd3 entry carrying it.
  await withFakeCanvas(async () => {
    const refFlux = { node_id: 60, widget_index: 0, original_path: 'flux/t5.safetensors', category: 'text_encoders',
      source_url: 'https://workflow/flux-link' };
    const refSd3 = { node_id: 60, widget_index: 1, original_path: 'sd3/t5.safetensors', category: 'text_encoders',
      source_url: null };
    const nodeDual = { properties: { models: [{ name: 'flux/t5.safetensors', url: 'https://workflow/flux-link', directory: 'text_encoders' }] } };
    const linker = { liveNodesFor: (ref) => ((ref === refFlux || ref === refSd3) ? [nodeDual] : []) };
    const analyzed = exportRows({ present_models: [refFlux, refSd3] });
    check('the analyzer-supplied sd3 row has no link of flux\'s',
          analyzed.find((r) => r.original_path === 'sd3/t5.safetensors')?.url === '');
    const dialog = Object.create(ExportDialog.prototype);
    dialog.rows = analyzed;
    dialog.linker = linker;
    dialog.option = (id) => id === 'properties';
    dialog.close = () => {};
    dialog.request = async (path, body) => {
      if (path !== '/model_linker/export/lookup') throw new Error('unexpected request: ' + path);
      return { links: body.models.map((m) => ({ url: m.existing_url })) };
    };
    await dialog.apply();
    const dualModels = nodeDual.properties.models;
    check('analyzer rows: flux keeps its own link',
          dualModels.find((m) => m.name === 'flux/t5.safetensors')?.url === 'https://workflow/flux-link');
    check('analyzer rows: no sd3 entry is written with flux\'s link',
          dualModels.length === 1 && !dualModels.some((m) => m.name === 'sd3/t5.safetensors'));
  });

  // Then with a link the user pasted for sd3: added as its own entry
  await withFakeCanvas(async () => {
    const refFlux = { node_id: 60, widget_index: 0, original_path: 'flux/t5.safetensors', category: 'text_encoders' };
    const refSd3 = { node_id: 60, widget_index: 1, original_path: 'sd3/t5.safetensors', category: 'text_encoders' };
    const nodeDual = { properties: { models: [{ name: 'flux/t5.safetensors', url: 'https://workflow/flux-link', directory: 'text_encoders' }] } };
    const linker = { liveNodesFor: (ref) => ((ref === refFlux || ref === refSd3) ? [nodeDual] : []) };
    const rowFlux = { key: 'text_encoders|flux/t5.safetensors', name: 't5.safetensors', category: 'text_encoders', original_path: 'flux/t5.safetensors',
      url: 'https://workflow/flux-link', sha256: '', present: true, refs: [refFlux] };
    const rowSd3 = { key: 'text_encoders|sd3/t5.safetensors', name: 't5.safetensors', category: 'text_encoders', original_path: 'sd3/t5.safetensors',
      url: 'https://user:pw@host/sd3-new?token=T', sha256: '', present: true, refs: [refSd3] };
    const dialog = Object.create(ExportDialog.prototype);
    dialog.rows = [rowFlux, rowSd3];
    dialog.linker = linker;
    dialog.option = (id) => id === 'properties';   // write links ON; note and node off
    dialog.close = () => {};
    const CLEAN = {
      'https://workflow/flux-link': 'https://workflow/flux-link',
      'https://user:pw@host/sd3-new?token=T': 'https://host/sd3-new',
    };
    dialog.request = async (path, body) => {
      if (path !== '/model_linker/export/lookup') throw new Error('unexpected request: ' + path);
      return { links: body.models.map((m) => (m.existing_url in CLEAN ? { url: CLEAN[m.existing_url] } : null)) };
    };
    await dialog.apply();
    const dualModels = nodeDual.properties.models;
    check('a DualCLIPLoader-shaped flux entry keeps its own link',
          dualModels.find((m) => m.name === 'flux/t5.safetensors')?.url === 'https://workflow/flux-link');
    check('an sd3 entry is added rather than overwriting flux\'s', dualModels.length === 2);
    check('the added sd3 entry carries the server\'s clean link',
          dualModels.find((m) => m.name === 'sd3/t5.safetensors')?.url === 'https://host/sd3-new');
  });

  // Scenario 4 (item 2a, real close()): closing the dialog - as a tab switch
  // now does - while apply() is still awaiting the lookup must stop it from
  // writing anything, and must release the in-flight guard.
  await withFakeCanvas(async ({ graph }) => {
    const refC = { node_id: 30, widget_index: 0, original_path: 'c.safetensors', category: 'loras' };
    const nodeC = { properties: {} };
    const linker = { liveNodesFor: (ref) => (ref === refC ? [nodeC] : []) };
    const rowC = { key: 'loras|c.safetensors', name: 'c.safetensors', category: 'loras', original_path: 'c.safetensors',
      url: 'https://host/c?token=T', sha256: '', present: false, refs: [refC] };
    const dialog = Object.create(ExportDialog.prototype);   // real close(), from the prototype
    dialog.rows = [rowC];
    dialog.linker = linker;
    dialog.option = () => true;
    dialog.element = { style: {} };
    // Only the lookup is held open under test control; anything else (e.g. a
    // markdown request an unfixed apply() might still reach) resolves right
    // away, so this cannot hang regardless of which code is under test.
    let release;
    dialog.request = (path) => path === "/model_linker/export/lookup"
      ? new Promise((resolve) => { release = () => resolve({ links: [{ url: 'https://host/c' }] }); })
      : Promise.resolve({ markdown: '## Model links' });

    const applying = dialog.apply();
    dialog.close();        // e.g. onGraphLoaded(), mid-flight
    release();
    await applying;

    check('closing mid-apply clears the dialog\'s rows', dialog.rows.length === 0);
    check('an apply superseded by close() writes nothing to the loader node',
          !(Array.isArray(nodeC.properties?.models) && nodeC.properties.models.length > 0));
    check('the in-flight guard is released even when superseded', dialog._applying === false);
  });

  // Scenario 5 (item 4): an existing http mirror link, with no credential,
  // must survive even with "write links into the loader nodes" off - that
  // option is about ADDING a new entry, not about keeping an existing one.
  await withFakeCanvas(async () => {
    const refM = { node_id: 40, widget_index: 0, original_path: 'y.safetensors', category: 'checkpoints' };
    const nodeM = { properties: { models: [{ name: 'y.safetensors', url: 'http://mirror.lan/y.safetensors', directory: 'checkpoints' }] } };
    const linker = { liveNodesFor: (ref) => (ref === refM ? [nodeM] : []) };
    const rowM = { key: 'checkpoints|y.safetensors', name: 'y.safetensors', category: 'checkpoints', original_path: 'y.safetensors',
      url: 'http://mirror.lan/y.safetensors', sha256: '', present: true, refs: [refM] };
    const dialog = Object.create(ExportDialog.prototype);
    dialog.rows = [rowM];
    dialog.linker = linker;
    dialog.option = () => false;   // "write links" off
    dialog.close = () => {};
    dialog.request = async (path, body) => {
      if (path === '/model_linker/export/lookup') {
        return { links: body.models.map((m) => ({ url: m.existing_url, source: 'workflow', url_source: 'workflow' })) };
      }
      throw new Error('unexpected request: ' + path);
    };
    await dialog.apply();
    check('an existing http mirror link survives with "write links" off',
          nodeM.properties.models[0].url === 'http://mirror.lan/y.safetensors');
  });

  // Scenario 6 (item 5): an existing note must be left alone - not emptied -
  // when the "note" option itself is off, whatever is or isn't linked.
  await withFakeCanvas(async ({ graph }) => {
    const existingNote = { type: 'MarkdownNote', pos: [0, 0], size: [1, 1], properties: { model_linker_note: true },
      widgets: [{ value: 'old content' }], widgets_values: ['old content'], mode: 0, graph: null };
    graph.add(existingNote);
    const refN = { node_id: 50, widget_index: 0, original_path: 'z.safetensors', category: 'loras' };
    const nodeN = { properties: {} };
    const linker = { liveNodesFor: (ref) => (ref === refN ? [nodeN] : []) };
    const rowN = { key: 'loras|z.safetensors', name: 'z.safetensors', category: 'loras', original_path: 'z.safetensors',
      url: '', sha256: '', present: false, refs: [refN] };
    const dialog = Object.create(ExportDialog.prototype);
    dialog.rows = [rowN];
    dialog.linker = linker;
    dialog.option = () => false;   // note (and everything else) off
    dialog.close = () => {};
    dialog.request = async (path) => { throw new Error('nothing to resolve: ' + path); };
    await dialog.apply();
    check('an existing note is left untouched when the "note" option is off',
          existingNote.widgets_values[0] === 'old content');
  });

  // Scenario 8 (item 3): an entry no ref claims at all still carries
  // whatever link the workflow shipped with - the binding focus (no token
  // in the shared workflow) makes no exception for it. Covers an http(s)
  // credentialed orphan (scrubbed via the server, in the SAME batch as
  // everything else - no extra network round-trip) and non-http ones (kept
  // if clean, dropped if not - strip_secrets itself never touches that
  // scheme, so there is nothing for the server to do with them).
  await withFakeCanvas(async () => {
    const refUsed = { node_id: 70, widget_index: 0, original_path: 'used.safetensors', category: 'loras' };
    const nodeShared = { properties: { models: [
      { name: 'used.safetensors', url: 'https://h/used', directory: 'loras' },
      { name: 'orphan.safetensors', url: 'https://h/orphan?token=OLD', directory: 'loras' },
      { name: 'clean.safetensors', url: 's3://bucket/clean.safetensors', directory: 'loras' },
      { name: 'risky.safetensors', url: 's3://user:pass@bucket/risky.safetensors?x=1', directory: 'loras' },
    ] } };
    const linker = { liveNodesFor: (ref) => (ref === refUsed ? [nodeShared] : []) };
    const rowUsed = { key: 'loras|used.safetensors', name: 'used.safetensors', category: 'loras', original_path: 'used.safetensors',
      url: 'https://h/used', sha256: '', present: true, refs: [refUsed] };   // same as its own entry - isolates the orphan behaviour
    const dialog = Object.create(ExportDialog.prototype);
    dialog.rows = [rowUsed];
    dialog.linker = linker;
    dialog.option = () => false;   // "write links" (and everything else) off - orphans are scrubbed regardless
    dialog.close = () => {};
    const CLEAN = { 'https://h/used': 'https://h/used', 'https://h/orphan?token=OLD': 'https://h/orphan' };
    dialog.request = async (path, body) => {
      if (path !== '/model_linker/export/lookup') throw new Error('unexpected request: ' + path);
      return { links: body.models.map((m) => (m.existing_url in CLEAN ? { url: CLEAN[m.existing_url] } : null)) };
    };
    await dialog.apply();
    const models = nodeShared.properties.models;
    check('the matched entry is left as is', models.find((m) => m.name === 'used.safetensors')?.url === 'https://h/used');
    check('an http(s) orphan is scrubbed of its credential',
          models.find((m) => m.name === 'orphan.safetensors')?.url === 'https://h/orphan');
    check('a clean non-http orphan survives untouched',
          models.find((m) => m.name === 'clean.safetensors')?.url === 's3://bucket/clean.safetensors');
    check('a risky non-http orphan is dropped',
          !('url' in models.find((m) => m.name === 'risky.safetensors')));
  });

  // Scenario 9 (item 4): "write links into the loader nodes" governs
  // whether a MATCHED entry may be REPLACED with a different link (one the
  // user pasted, found by hash, or found online) - never whether its own
  // existing credential gets scrubbed, which always happens regardless.
  await withFakeCanvas(async () => {
    const CLEAN = {
      'https://h/original?token=T': 'https://h/original',
      'https://user:pw@host/different-link?token=NEW': 'https://host/different-link',
    };
    const makeCase = () => {
      const ref = { node_id: 80, widget_index: 0, original_path: 'm.safetensors', category: 'checkpoints' };
      const node = { properties: { models: [{ name: 'm.safetensors', url: 'https://h/original?token=T', directory: 'checkpoints' }] } };
      const linker = { liveNodesFor: (r) => (r === ref ? [node] : []) };
      const row = { key: 'checkpoints|m.safetensors', name: 'm.safetensors', category: 'checkpoints', original_path: 'm.safetensors',
        url: 'https://user:pw@host/different-link?token=NEW', sha256: '', present: true, refs: [ref] };
      const dialog = Object.create(ExportDialog.prototype);
      dialog.rows = [row];
      dialog.linker = linker;
      dialog.close = () => {};
      dialog.request = async (path, body) => {
        if (path !== '/model_linker/export/lookup') throw new Error('unexpected request: ' + path);
        return { links: body.models.map((m) => (m.existing_url in CLEAN ? { url: CLEAN[m.existing_url] } : null)) };
      };
      return { node, dialog };
    };

    const off = makeCase();
    off.dialog.option = () => false;
    await off.dialog.apply();
    check('with "write links" off, a different resolved link never replaces the existing one',
          off.node.properties.models[0].url === 'https://h/original');

    const on = makeCase();
    on.dialog.option = (id) => id === 'properties';   // isolate the option under test - note/node stay off
    await on.dialog.apply();
    check('with "write links" on, the resolved link replaces the existing one, as before',
          on.node.properties.models[0].url === 'https://host/different-link');
  });

  // Scenario 9b (item 4, a non-http matched entry): a MATCHED entry whose
  // own link is non-http (s3://...) never becomes a lookup candidate in the
  // first place - safeHttpUrl rejects that scheme, so its row's url is "" by
  // default, not because anyone cleared anything. With "write links" off it
  // must be judged the same way an orphan's non-http link is - kept if
  // clean, dropped if not - never deleted outright just because there was
  // nothing to compare it against.
  await withFakeCanvas(async () => {
    const refClean = { node_id: 90, widget_index: 0, original_path: 'clean-match.safetensors', category: 'checkpoints' };
    const nodeClean = { properties: { models: [{ name: 'clean-match.safetensors', url: 's3://bucket/clean-match.safetensors', directory: 'checkpoints' }] } };
    const linkerClean = { liveNodesFor: (r) => (r === refClean ? [nodeClean] : []) };
    const rowClean = { key: 'checkpoints|clean-match.safetensors', name: 'clean-match.safetensors', category: 'checkpoints',
      original_path: 'clean-match.safetensors', url: '', sha256: '', present: true, refs: [refClean] };
    const dialogClean = Object.create(ExportDialog.prototype);
    dialogClean.rows = [rowClean];
    dialogClean.linker = linkerClean;
    dialogClean.option = () => false;
    dialogClean.close = () => {};
    dialogClean.request = async (path) => { throw new Error('nothing to resolve: ' + path); };
    await dialogClean.apply();
    check('a matched entry with a clean non-http link survives with "write links" off',
          nodeClean.properties.models[0].url === 's3://bucket/clean-match.safetensors');

    const refRisky = { node_id: 91, widget_index: 0, original_path: 'risky-match.safetensors', category: 'checkpoints' };
    const nodeRisky = { properties: { models: [{ name: 'risky-match.safetensors', url: 's3://user:pass@bucket/risky-match.safetensors?x=1', directory: 'checkpoints' }] } };
    const linkerRisky = { liveNodesFor: (r) => (r === refRisky ? [nodeRisky] : []) };
    const rowRisky = { key: 'checkpoints|risky-match.safetensors', name: 'risky-match.safetensors', category: 'checkpoints',
      original_path: 'risky-match.safetensors', url: '', sha256: '', present: true, refs: [refRisky] };
    const dialogRisky = Object.create(ExportDialog.prototype);
    dialogRisky.rows = [rowRisky];
    dialogRisky.linker = linkerRisky;
    dialogRisky.option = () => false;
    dialogRisky.close = () => {};
    dialogRisky.request = async (path) => { throw new Error('nothing to resolve: ' + path); };
    await dialogRisky.apply();
    check('a matched entry with a risky non-http link is dropped with "write links" off',
          !('url' in nodeRisky.properties.models[0]));
  });

  // --- Fix round 4 ---------------------------------------------------------
  //
  // A stand-in for the server's strip_secrets, faithful where it matters:
  // like Python's urlparse, it finds userinfo only after a literal "//", so a
  // backslash-form link sent raw keeps its userinfo (and the browser then
  // reads it as https://user:pw@host/...).
  const fakeStrip = (u) => {
    if (typeof u !== 'string') return null;
    const m = /^(https?:)\/\/(?:[^@/?#]*@)?([^?#;]*)/i.exec(u);
    return m ? m[1] + '//' + m[2] : u.replace(/[?#].*$/, '');
  };
  const makeDialog = ({ rows: dRows = [], linker = { liveNodesFor: () => [] }, option = () => false, request }) => {
    const d = Object.create(ExportDialog.prototype);
    d.rows = dRows;
    d.linker = linker;
    d.option = option;
    d.close = () => {};
    d.request = request;
    return d;
  };
  const stripRequest = (calls = []) => async (path, body) => {
    if (path !== '/model_linker/export/lookup') throw new Error('unexpected request: ' + path);
    calls.push(body.models);
    if (body.models.length > 500) throw new Error('models must be a list of at most 500');
    return { links: body.models.map((m) => (m.existing_url ? { url: fakeStrip(m.existing_url) } : null)) };
  };
  const leaks = (url) => typeof url === 'string' && (/token|SECRET|pw@/i.test(url));

  // D1: nodes no ref resolves to - an unrecognised widget's node, a node
  // inside a subgraph instance's inner graph, and a node inside a subgraph
  // definition with no instance at all - still ship properties.models.
  // Every one is scrubbed, inside the same undo step and the same batch.
  // A node no row touched is never added to or replaced, even when its
  // entry shares a name with a row that has a new link.
  await withFakeCanvas(async ({ graph, canvasCalls }) => {
    const loose = { properties: { models: [{ name: 'u.safetensors', url: 'https://h/u?token=T', directory: 'loras', hash: 'ab' }] } };
    const sameName = { properties: { models: [{ name: 'shared.safetensors', url: 'https://h/old?token=X', directory: 'loras' }] } };
    const innerNode = { properties: { models: [{ name: 'i.safetensors', url: 'https://user:pw@h/i', directory: 'vae' }] } };
    const instance = { type: 'some-subgraph-uuid', properties: {}, subgraph: { _nodes: [innerNode] } };
    const defNode = { properties: { models: [{ name: 'd.safetensors', url: 'https://h/d?token=D', directory: 'vae' }] } };
    graph.add(loose); graph.add(sameName); graph.add(instance);
    graph.subgraphs = new Map([['unused-def', { nodes: [defNode] }]]);
    const refS = { node_id: 99, widget_index: 0, original_path: 'shared.safetensors', category: 'loras' };
    const nodeS = { properties: {} };
    const rowS = { key: 'loras|shared.safetensors', name: 'shared.safetensors', category: 'loras', original_path: 'shared.safetensors',
      url: 'https://h/new', sha256: '', present: true, refs: [refS] };
    const calls = [];
    const dialog = makeDialog({ rows: [rowS], linker: { liveNodesFor: (r) => (r === refS ? [nodeS] : []) },
      option: (id) => id === 'properties', request: stripRequest(calls) });
    await dialog.apply();
    check('D1: a node no ref resolves to is scrubbed', loose.properties.models[0].url === 'https://h/u');
    check('D1: only url is touched on it', loose.properties.models[0].hash === 'ab');
    check('D1: a node inside a subgraph instance is scrubbed', innerNode.properties.models[0].url === 'https://h/i');
    check('D1: a node in a subgraph definition with no instance is scrubbed', defNode.properties.models[0].url === 'https://h/d');
    check('D1: an untouched node\'s entry is scrubbed, never replaced by a row\'s link',
          sameName.properties.models.length === 1 && sameName.properties.models[0].url === 'https://h/old');
    check('D1: the row\'s own node still gets its new link', nodeS.properties.models?.[0]?.url === 'https://h/new');
    check('D1: still one undo step and one lookup request', canvasCalls.join(',') === 'before,after' && calls.length === 1);
  });

  // D2 / D3: a url that is not a string, a `;` path parameter on a non-http
  // URL, and backslash-form https (which a browser reads as
  // https://user:pw@host/...) - on an orphan and on a pasted row.
  await withFakeCanvas(async ({ graph }) => {
    const node = { properties: { models: [
      { name: 'arr.safetensors', url: ['https://h/a?token=T'] },
      { name: 'obj.safetensors', url: { href: 'https://h/o?token=T' } },
      { name: 's3.safetensors', url: 's3://bucket/a;token=SECRET/b' },
      { name: 'bs.safetensors', url: 'https:\\\\user:pw@h\\bs?token=T' },
    ] } };
    graph.add(node);   // reached by the graph walk only - every entry an orphan
    const refR = { node_id: 7, widget_index: 0, original_path: 'r.safetensors', category: 'loras' };
    const nodeR = { properties: {} };
    const rowR = { key: 'loras|r.safetensors', name: 'r.safetensors', category: 'loras', original_path: 'r.safetensors',
      url: 'https:\\\\user:pw@host\\r?token=T', sha256: '', present: true, refs: [refR] };
    const calls = [];
    const dialog = makeDialog({ rows: [rowR], linker: { liveNodesFor: (r) => (r === refR ? [nodeR] : []) },
      option: (id) => id === 'properties', request: stripRequest(calls) });
    await dialog.apply();
    const by = (n) => node.properties.models.find((m) => m.name === n);
    check('D2: an array url is deleted', !('url' in by('arr.safetensors')));
    check('D2: an object url is deleted', !('url' in by('obj.safetensors')));
    check('D2: nothing is sent to the server without a link to strip',
          calls.flat().every((m) => typeof m.existing_url === 'string'));
    check('D3: a non-http url with a ; path parameter is deleted', !('url' in by('s3.safetensors')));
    check('D3: a backslash-form orphan is stripped, not kept raw', by('bs.safetensors').url === 'https://h/bs');
    check('D3: a pasted backslash-form link is written without its userinfo',
          nodeR.properties.models?.[0]?.url === 'https://host/r');
    const written = [...node.properties.models, ...(nodeR.properties.models || [])].map((m) => m.url);
    check('D2/D3: no written url carries a credential', !written.some(leaks));
  });

  // B: the graph changes while apply() awaits the lookup - entries
  // reordered, or removed outright. Each write must still land on the entry
  // it was meant for, and a vanished one is skipped, not a TypeError.
  await withFakeCanvas(async () => {
    const node = { properties: { models: [
      { name: 'p.safetensors', url: 'https://h/p?token=T' },
      { name: 'q.safetensors', url: 'https://h/q?token=U' },
      { name: 'o.safetensors', url: 'https://h/o?token=V' },
    ] } };
    const refP = { node_id: 1, widget_index: 0, original_path: 'p.safetensors', category: 'loras' };
    const refQ = { node_id: 1, widget_index: 1, original_path: 'q.safetensors', category: 'loras' };
    const row = (ref) => ({ key: `loras|${ref.original_path}`, name: ref.original_path, category: 'loras',
      original_path: ref.original_path, url: node.properties.models.find((m) => m.name === ref.original_path).url,
      sha256: '', present: true, refs: [ref] });
    const strip = stripRequest();
    const reorder = makeDialog({ rows: [row(refP), row(refQ)], linker: { liveNodesFor: () => [node] },
      option: (id) => id === 'properties',
      request: async (path, body) => { node.properties.models.reverse(); return strip(path, body); } });
    await reorder.apply();
    const by = (n) => node.properties.models.find((m) => m.name === n)?.url;
    check('B: a reordered matched entry gets its own link',
          by('p.safetensors') === 'https://h/p' && by('q.safetensors') === 'https://h/q');
    check('B: a reordered orphan gets its own link', by('o.safetensors') === 'https://h/o');

    const gone = { properties: { models: [{ name: 'p.safetensors', url: 'https://h/p?token=T' }, { name: 'o.safetensors', url: 'https://h/o?token=V' }] } };
    const refG = { node_id: 2, widget_index: 0, original_path: 'p.safetensors', category: 'loras' };
    const rowG = { key: 'loras|p.safetensors', name: 'p.safetensors', category: 'loras', original_path: 'p.safetensors',
      url: 'https://h/p?token=T', sha256: '', present: true, refs: [refG] };
    const removing = makeDialog({ rows: [rowG], linker: { liveNodesFor: () => [gone] }, option: (id) => id === 'properties',
      request: async (path, body) => { gone.properties.models.splice(0); return strip(path, body); } });
    let threw = null;
    try { await removing.apply(); } catch (error) { threw = error; }
    check('B: an entry removed during the lookup is skipped, not a TypeError', threw === null);
    check('B: nothing is re-added in its place', gone.properties.models.length === 0);
  });

  // C: more links than one /export/lookup accepts (500) - batched, with
  // links[i] still answering models[i] across batches; both from apply()
  // and from the lookUp() run when the dialog opens.
  await withFakeCanvas(async () => {
    const N = 501;
    const nodes = [];
    const bigRows = [];
    for (let i = 0; i < N; i++) {
      const ref = { node_id: i, widget_index: 0, original_path: `m${i}.safetensors`, category: 'loras' };
      nodes.push({ properties: {} });
      bigRows.push({ key: `loras|m${i}.safetensors`, name: `m${i}.safetensors`, category: 'loras', original_path: `m${i}.safetensors`,
        url: `https://h/m${i}?token=T`, sha256: '', present: false, refs: [ref] });
    }
    const calls = [];
    const dialog = makeDialog({ rows: bigRows, linker: { liveNodesFor: (r) => [nodes[r.node_id]] },
      option: (id) => id === 'properties', request: stripRequest(calls) });
    await dialog.apply();
    check('C: apply batches the lookup within the server cap', calls.length === 2 && calls.every((c) => c.length <= 500));
    check('C: every node gets its own link across batches',
          nodes.every((n, i) => n.properties.models?.[0]?.url === `https://h/m${i}`));

    const lookCalls = [];
    const opened = makeDialog({ rows: bigRows.map((r) => ({ ...r, url: '' })), request: async (path, body) => {
      lookCalls.push(body);
      if (body.models.length > 500) throw new Error('too many');
      return { links: body.models.map((m) => ({ url: `https://found/${m.filename}`, url_source: 'huggingface' })), searched: true };
    } });
    opened.element = { querySelector: () => ({ style: {} }) };
    await opened.lookUp({ manual: true });
    check('C: lookUp batches too, passing manual on every batch',
          lookCalls.length === 2 && lookCalls.every((b) => b.models.length <= 500 && b.manual === true));
    check('C: lookUp answers each row from its own batch',
          opened.rows[0].url === 'https://found/m0.safetensors' && opened.rows[500].url === 'https://found/m500.safetensors');
  });

  // --- Fix round 5 ---------------------------------------------------------
  //
  // The note cannot be built (/export/markdown refuses more than 500 models
  // with a 400): the node links - added and scrubbed - are still written in
  // the one undo step, the existing note is left as it was, and the failure
  // is reported as a toast.
  await withFakeCanvas(async ({ graph, canvasCalls }) => {
    const oldNote = { type: 'MarkdownNote', pos: [0, 0], size: [1, 1], properties: { model_linker_note: true },
      widgets: [{ value: 'old note' }], widgets_values: ['old note'], mode: 0, graph: null };
    const loose = { properties: { models: [{ name: 'l.safetensors', url: 'https://h/l?token=T' }] } };
    graph.add(oldNote); graph.add(loose);
    const refN = { node_id: 5, widget_index: 0, original_path: 'n.safetensors', category: 'loras' };
    const nodeN = { properties: {} };
    const rowN = { key: 'loras|n.safetensors', name: 'n.safetensors', category: 'loras', original_path: 'n.safetensors',
      url: 'https://h/n?token=T', sha256: '', present: true, refs: [refN] };
    const strip = stripRequest();
    const dialog = makeDialog({ rows: [rowN], linker: { liveNodesFor: (r) => (r === refN ? [nodeN] : []) },
      option: () => true,
      request: async (path, body) => {
        if (path === '/model_linker/export/markdown') throw new Error('models must be a list');
        return strip(path, body);
      } });
    globalThis.__toasts = [];
    await dialog.apply();
    check('note failure: the node link is still added', nodeN.properties.models?.[0]?.url === 'https://h/n');
    check('note failure: an orphan is still scrubbed', loose.properties.models[0].url === 'https://h/l');
    check('note failure: the existing note is left as it was, and no second one added',
          oldNote.widgets_values[0] === 'old note' && graph._nodes.filter((n) => n.properties?.model_linker_note).length === 1);
    check('note failure: still one undo step', canvasCalls.join(',') === 'before,after');
    check('note failure: reported as an error toast',
          globalThis.__toasts.some((t) => t.severity === 'error' && /links note/.test(t.summary)));
  });

  // A ';' anywhere in a non-http link is unclean - a non-special scheme's
  // host is opaque, so s3://bucket;token=X/a carries it outside the path.
  await withFakeCanvas(async ({ graph }) => {
    check('cleanNonHttpUrl rejects ; in an opaque host', ExportDialog.cleanNonHttpUrl('s3://bucket;token=X/a') === false);
    check('cleanNonHttpUrl still keeps a clean s3 link', ExportDialog.cleanNonHttpUrl('s3://bucket/a') === true);
    const node = { properties: { models: [{ name: 'h.safetensors', url: 's3://bucket;token=X/a' }] } };
    graph.add(node);
    const dialog = makeDialog({ request: async (path) => { throw new Error('nothing to send: ' + path); } });
    await dialog.apply();
    check('a non-http orphan with ; in its host is deleted', !('url' in node.properties.models[0]));
  });

  // Scenario 7 (item 2b, faithful transcription - LinkerManagerDialog needs a
  // live DOM this harness does not have, same reason test_analyze_race.mjs
  // uses a stub rather than the real class): a tab switch while openExport()
  // is still awaiting loadWorkflowData() must stop it from showing the old
  // tab's rows - but a bare abort would leave the MAIN dialog, if open,
  // stuck on "Analyzing workflow..." forever. onGraphLoaded() instead starts
  // a fresh loadWorkflowData() whenever one is in flight; that call aborts
  // the old request itself (its own first line) before issuing a new one,
  // so openExport()'s pending call still resolves to null, while the dialog
  // completes a fresh render instead of hanging.
  {
    const renders = [];   // every loadWorkflowData() call that actually rendered
    const dialog = {
      analyzeAbort: null,
      exportDialog: null,
      async loadWorkflowData() {
        if (this.analyzeAbort) this.analyzeAbort.abort();
        const abort = new AbortController();
        this.analyzeAbort = abort;
        const tag = `data-${++dialog.callCount}`;
        try {
          const data = await new Promise((resolve, reject) => {
            const timer = setTimeout(() => resolve({ tag }), 30);
            abort.signal.addEventListener('abort', () => {
              clearTimeout(timer);
              reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
            });
          });
          if (this.analyzeAbort !== abort) return null;
          renders.push(tag);
          return data;
        } catch (error) {
          if (error.name === 'AbortError' || this.analyzeAbort !== abort) return null;
          throw error;
        } finally {
          if (this.analyzeAbort === abort) this.analyzeAbort = null;
        }
      },
      callCount: 0,
      show() {},
      async openExport() {
        const data = await this.loadWorkflowData();
        if (!data) return;
        if (!this.exportDialog) this.exportDialog = { shown: 0, show(d) { this.shown++; this.data = d; } };
        this.exportDialog.show(data);
      },
    };
    const modelLinker = {
      dialog,
      onGraphLoaded() {
        this.dialog?.exportDialog?.close?.();
        if (this.dialog?.analyzeAbort) this.dialog.loadWorkflowData();
      },
    };
    const opening = dialog.openExport();
    await new Promise((resolve) => setTimeout(resolve, 5));
    modelLinker.onGraphLoaded();   // tab switch, while the first analysis is still in flight
    await opening;
    await new Promise((resolve) => setTimeout(resolve, 40));   // let the fresh analysis settle too

    check('a tab switch stops an in-flight openExport from showing stale rows',
          dialog.exportDialog === null);
    check('the main dialog is not left stuck on "Analyzing workflow..." - a fresh render completes',
          renders.includes('data-2'));
  }

  // --- source-level checks for what is impractical to drive end-to-end ----
  const src = readSource('modules/export-dialog.js');
  check('applying ignores a double press', /this\._applying/.test(src));
  check('finding by hash ignores a double press on the same row', /this\._hashBusy/.test(src));
  check('findByHash bails once the row it was called for is stale', (src.match(/this\.rows\[i\] !== row/g) || []).length >= 3);
  check('an existing Model Linker node is re-muted on re-apply', /existingMarker\.mode = 2;/.test(src));
  check('a re-apply with no links empties a stale note', /existingNote\.widgets_values = \[""\];/.test(src));
  check('touching the note at all is gated by the "note" option',
        /if \(this\.option\("note"\)\) \{\s*if \(markdown\)/.test(src));
  check('close() invalidates the dialog\'s rows', /close\(\) \{[\s\S]{0,200}?this\.rows = \[\];/.test(src));
  check('the basename fallback is guarded against entries another ref already claims',
        /findModelEntry\(models, name, claimed\)/.test(src) && /!claimed\?\.has\(i\)/.test(src));
  check('orphan entries (no ref claims them) are collected for scrubbing', /const orphans = \[\];/.test(src));
  check('a matched entry\'s own link is only needed when "write links" is off',
        /if \(!writeLinksOn\) \{/.test(src) && /selfCandidates/.test(src));
  check('names and urls are escaped', /escapeHtml\(row\.name\)/.test(src) && /escapeHtml\(row\.url/.test(src));
  check('lookups can be asked for by hand', /lookUp\(\{ manual: true \}\)/.test(src));

  const linkerSrc = readSource('linker.js');
  check('the export dialog is closed when another workflow loads',
        /onGraphLoaded\(\)\s*\{\s*this\.dialog\?\.exportDialog\?\.close\(\);/.test(linkerSrc));
  check('a tab switch re-analyses rather than leaving the dialog stuck, when one is in flight',
        /onGraphLoaded\(\)\s*\{[\s\S]{0,300}?if \(this\.dialog\?\.analyzeAbort\) this\.dialog\.loadWorkflowData\(\);/
          .test(linkerSrc));
  check('opening Export reports a failure instead of throwing', /notifyError\("Could not open Export"/.test(linkerSrc));
  check('opening Export is async and awaits the dialog, so a rejection cannot escape unhandled',
        /async openExport\(\)[\s\S]{0,700}?await this\.dialog\.openExport\(\)/.test(linkerSrc));

  const dialog = readSource('modules/linker-dialog.js');
  check('export analyses the workflow on screen', /const data = await this\.loadWorkflowData\(\);/.test(dialog));
  check('the analysis is returned to the caller',
        /this\.displayMissingModels\(this\.contentElement, data\);\s*return data;/.test(dialog));
  // (autoResolve100Percent has a local `analyzeData`; only the field is banned)
  check('no earlier analysis is kept around', !/this\.analyzeData/.test(dialog));
  return check.failures;
}
