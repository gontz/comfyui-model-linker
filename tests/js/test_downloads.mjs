// What the download block shows, and the rules that keep it cheap and safe:
// results patch cards in place, one download per *file* (not per card), and
// nothing unescaped. The behavioural half of this suite runs the real
// DownloadController against a fake server and a fake DOM container - the
// same shape as the probe used to find the download-to-card identity bugs
// this suite pins down (I-1 through I-4, M-2 through M-4).
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createChecker, loadModule, readSource } from './harness.mjs';

export const name = 'downloads';

const HERE = dirname(fileURLToPath(import.meta.url));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Node has no CSS global; refresh() only needs .escape() to be the identity
// for ids built from refSlot(), which already restricts itself to
// [A-Za-z0-9_-] and therefore needs no real escaping in these tests.
globalThis.CSS = globalThis.CSS || { escape: (s) => s };

function fakeContainer() {
  const els = new Map();
  return {
    els,
    querySelector(sel) {
      const id = sel.slice(1);
      if (!els.has(id)) els.set(id, { id, innerHTML: '' });
      return els.get(id);
    },
    contains: () => true,
    addEventListener() {},
  };
}

const HTTPS_SOURCE = { url: 'https://huggingface.co/a/b/resolve/main/m.safetensors', source: 'huggingface' };

const mk = (node, name, category, extra = {}) => ({
  node_id: node, widget_index: 0, original_path: name, category,
  lookup_state: 'found', download_source: HTTPS_SOURCE, matches: [], ...extra,
});

const fakeDialog = () => ({ loadWorkflowData: async () => {}, openSettings() {} });

const reply = (data, ok = true, status = 200) => ({ ok, status, json: async () => data });

export default async function run() {
  const check = createChecker(name);
  const { downloadState, isFinished, DownloadController } = await loadModule('modules/downloads.js');

  const https = { url: 'https://huggingface.co/a/b/resolve/main/x.safetensors', source: 'huggingface' };
  check('a found https source is downloadable',
        downloadState({ download_source: https, lookup_state: 'found' }).kind === 'available');
  check('an http source is not',
        downloadState({ download_source: { url: 'http://x/y' }, lookup_state: 'found' }).kind !== 'available');
  check('a javascript: source is not',
        downloadState({ download_source: { url: 'javascript:alert(1)' }, lookup_state: 'found' }).kind !== 'available');
  check('a perfect local match needs no download',
        downloadState({ matches: [{ confidence: 100 }], download_source: https }).kind === 'none');
  check('a wrong-folder 100% match still offers the download',
        downloadState({ matches: [{ confidence: 100, category_mismatch: true }], download_source: https }).kind === 'available');
  check('pending lookups show as searching', downloadState({ lookup_state: 'pending' }).kind === 'searching');
  check('a running download wins', downloadState({ download_source: https }, { status: 'downloading' }).kind === 'active');
  check('HF "completed" before verification is still active',
        downloadState({}, { status: 'completed' }).kind === 'active' && !isFinished({ status: 'completed' }));
  check('completed with a hash is finished', isFinished({ status: 'completed', sha256: 'ab' }));
  check('lookup_state undefined (lookups never ran) renders nothing, not a dead button',
        downloadState({}).kind === 'none');
  check('lookup_state "idle" (auto-lookup off) still offers a manual search',
        downloadState({ lookup_state: 'idle' }).kind === 'idle');

  const src = readSource('modules/downloads.js');
  const poll = src.slice(src.indexOf('async pollLookups'), src.indexOf('async searchAgain'));
  check('lookup results patch cards in place', /this\.refresh\(/.test(poll));
  check('lookup results never re-render the list',
        !/displayMissingModels|loadWorkflowData|innerHTML\s*=\s*[^;]*missing/.test(poll));
  check('download all starts one download per file', /seen\.has\(/.test(src.slice(src.indexOf('async downloadAll'))));
  check('reopening reconnects to running downloads', /async reconnect\(/.test(src) && /reconnect\(\)/.test(src));
  check('source names are escaped', /escapeHtml\(where\)/.test(src));
  check('workflow-derived slots are escaped in attributes',
        !/data-ml-slot="\$\{slot\}"/.test(src) && /data-ml-slot="\$\{escapeHtml\(slot\)\}"/.test(src)
        && /id="\$\{escapeHtml\(this\.blockId\(missing\)\)\}"/.test(src) && /CSS\.escape\(/.test(src));
  check('downloads are tracked by file identity, not by card',
        /this\.byFile/.test(src) && !/this\.byRef/.test(src));
  check('per-analysis state has its own reset, separate from stop()',
        /reset\(\)\s*\{/.test(src));
  check('fileKey falls back to the download source\'s own folder when the card\'s category is unknown',
        /effectiveCategory/.test(src) && /source\.canonical_category \|\| source\.directory/.test(src));
  check('reconnect only adopts records matching something on screen',
        /onScreen/.test(src.slice(src.indexOf('async reconnect'))));

  const dialog = readSource('modules/linker-dialog.js');
  check('closing the dialog stops polling', /close\(\)\s*\{[\s\S]{0,600}downloads\.stop\(\)/.test(dialog));
  check('the footer offers download all', /downloadAll\(\)/.test(dialog));
  {
    const lwdStart = dialog.indexOf('async loadWorkflowData');
    const lwdEnd = dialog.indexOf('Get current workflow from ComfyUI', lwdStart);
    const body = dialog.slice(lwdStart, lwdEnd);
    check('loadWorkflowData resets per-analysis download state at the top',
          /this\.downloads\.reset\(\)/.test(body.slice(0, 700)));
  }

  // ---- Behavioural: file identity, dedupe, reset, reconnect, timers ----
  // Imported directly rather than through loadModule() so mutating
  // api.fetchApi here is visible to downloads.js's own import of it: both
  // resolve to the same on-disk stub (tests/js/stubs/scripts/api.js), which
  // Node caches by URL regardless of which module asked for it first.
  const { api } = await import('file://' + join(HERE, 'stubs', 'scripts', 'api.js').replace(/\\/g, '/'));

  // 1. Five nodes referencing one model: one POST, every card shows progress.
  {
    const posts = [];
    const progress = {};
    api.fetchApi = async (path, opts = {}) => {
      const body = opts.body ? JSON.parse(opts.body) : undefined;
      if (path === '/model_linker/download') {
        posts.push(body);
        progress.d1 = { status: 'downloading', progress: 10, filename: body.filename, category: body.category };
        return reply({ download_id: 'd1' });
      }
      if (path === '/model_linker/progress') return reply(progress);
      return reply({});
    };
    const c = new DownloadController(fakeDialog());
    const cont = fakeContainer();
    const ms = [1, 2, 3, 4, 5].map((n) => mk(n, 'flux.safetensors', 'diffusion_models'));
    c.afterRender(cont, { missing_models: ms });
    await sleep(20);
    await c.downloadAll();
    const shown = [...cont.els.values()].filter((e) => /progress/.test(e.innerHTML)).length;
    check('five nodes referencing one model send one POST', posts.length === 1, `posts=${posts.length}`);
    check('all five cards show the shared download', shown === 5, `shown=${shown}`);
    c.stop();
  }

  // 2. Two alias categories (clip / text_encoders) for the same filename:
  // one POST, both cards show it, no spurious "already downloading" toast.
  {
    const posts = [];
    const progress = {};
    let nextId = 1;
    api.fetchApi = async (path, opts = {}) => {
      const body = opts.body ? JSON.parse(opts.body) : undefined;
      if (path === '/model_linker/download') {
        posts.push(body);
        const id = 'd' + nextId++;
        progress[id] = { status: 'downloading', progress: 5, filename: body.filename, category: body.category };
        return reply({ download_id: id });
      }
      if (path === '/model_linker/progress') return reply(progress);
      return reply({});
    };
    globalThis.__toasts = [];
    const c = new DownloadController(fakeDialog());
    const cont = fakeContainer();
    const ms = [
      mk(1, 'te.safetensors', 'clip', { canonical_category: 'text_encoders' }),
      mk(2, 'te.safetensors', 'text_encoders', { canonical_category: 'text_encoders' }),
    ];
    c.afterRender(cont, { missing_models: ms });
    await sleep(20);
    await c.downloadAll();
    const shown = [...cont.els.values()].filter((e) => /progress/.test(e.innerHTML)).length;
    check('clip and text_encoders collapse to one POST', posts.length === 1, `posts=${posts.length}`);
    check('both alias cards show the one download', shown === 2, `shown=${shown}`);
    check('no "already downloading" error toast for the alias card',
          !globalThis.__toasts.some((t) => /already downloading/i.test(t.detail || '')),
          JSON.stringify(globalThis.__toasts.map((t) => t.detail)));
    c.stop();
  }

  // 3. reset() - the dialog's loadWorkflowData() chokepoint - drops a fully
  // resolved workflow's stale state, so "Download all" starts nothing.
  {
    const posts = [];
    api.fetchApi = async (path, opts = {}) => {
      const body = opts.body ? JSON.parse(opts.body) : undefined;
      if (path === '/model_linker/download') { posts.push(body); return reply({ download_id: 'x' }); }
      if (path === '/model_linker/progress') return reply({});
      return reply({});
    };
    const c = new DownloadController(fakeDialog());
    c.afterRender(fakeContainer(), { missing_models: [mk(1, 'old.safetensors', 'loras')] });
    await sleep(20);
    c.stop();   // dialog closed
    c.reset();  // the next loadWorkflowData() finds nothing missing
    await c.downloadAll();
    check('download all on a fully-resolved workflow starts nothing', posts.length === 0, `posts=${JSON.stringify(posts)}`);
  }

  // 4. A new card landing on the same node/widget slot as a previous
  // analysis's card must not inherit that card's download.
  {
    const progress = {};
    api.fetchApi = async (path, opts = {}) => {
      const body = opts.body ? JSON.parse(opts.body) : undefined;
      if (path === '/model_linker/download') {
        progress.d1 = { status: 'downloading', progress: 50, filename: body.filename, category: body.category };
        return reply({ download_id: 'd1' });
      }
      if (path === '/model_linker/progress') return reply(progress);
      return reply({});
    };
    const c = new DownloadController(fakeDialog());
    c.afterRender(fakeContainer(), { missing_models: [mk(7, 'a.safetensors', 'loras')] });
    await sleep(20);
    await c.downloadAll();
    c.stop();
    c.reset();
    const other = mk(7, 'b.safetensors', 'vae'); // same node_id/widget_index, different file
    const html = c.renderBlock(other);
    check('a different model on the same slot shows no inherited progress or cancel',
          !/progress|Cancel/.test(html), html.slice(0, 160));
  }

  // 5. reconnect() attaches a running download only to the card whose
  // canonical file key matches - not to every card sharing a basename.
  {
    const progress = {
      dX: { status: 'downloading', progress: 30, filename: 'model.safetensors', category: 'vae', canonical_category: 'vae' },
    };
    api.fetchApi = async (path) => reply(path === '/model_linker/progress' ? progress : {});
    const c = new DownloadController(fakeDialog());
    const cont = fakeContainer();
    const ms = [mk(1, 'model.safetensors', 'vae'), mk(2, 'model.safetensors', 'text_encoders')];
    c.afterRender(cont, { missing_models: ms });
    await sleep(30);
    const shown = [...cont.els.values()].filter((e) => /progress/.test(e.innerHTML)).map((e) => e.id);
    check('reconnect attaches only the matching-category card',
          shown.length === 1 && shown[0] === c.blockId(ms[0]), `cards=${shown}`);
    c.stop();
  }

  // 6. A second "Download all found" click, with the first click's downloads
  // now active, must not repeat the "nothing to download" toast.
  {
    const progress = {};
    let nextId = 1;
    api.fetchApi = async (path, opts = {}) => {
      const body = opts.body ? JSON.parse(opts.body) : undefined;
      if (path === '/model_linker/download') {
        const id = 'd' + nextId++;
        progress[id] = { status: 'downloading', progress: 1, filename: body.filename, category: body.category };
        return reply({ download_id: id });
      }
      if (path === '/model_linker/progress') return reply(progress);
      return reply({});
    };
    globalThis.__toasts = [];
    const c = new DownloadController(fakeDialog());
    c.afterRender(fakeContainer(), { missing_models: [mk(1, 'z.safetensors', 'loras')] });
    await sleep(20);
    await c.downloadAll();
    await c.downloadAll();
    check('the second click adds no false "nothing to download" toast',
          globalThis.__toasts.length === 0, JSON.stringify(globalThis.__toasts.map((t) => t.detail)));
    c.stop();
  }

  // 7. Closing mid-request must not leave a leaked polling chain (0552199).
  // The poll interval is 1000ms, so this has to wait past at least one more
  // tick - anything shorter can't actually catch a re-armed timer.
  {
    let calls = 0;
    const progress = { dY: { status: 'downloading', progress: 30, filename: 'y.safetensors', category: 'loras' } };
    api.fetchApi = async (path) => {
      if (path === '/model_linker/progress') {
        calls++;
        await sleep(150);
        return reply(progress);
      }
      return reply({});
    };
    const c = new DownloadController(fakeDialog());
    c.afterRender(fakeContainer(), { missing_models: [mk(1, 'y.safetensors', 'loras')] }); // reconnect() in flight
    await sleep(30);
    c.stop(); // closed while that request is still in flight
    const callsAtClose = calls;
    await sleep(1300);
    check('no further /progress calls after closing mid-request',
          calls === callsAtClose, `${callsAtClose} -> ${calls}`);
    check('no timer is left armed after close',
          c.progressTimer === null && c.lookupTimer === null,
          `progressTimer=${c.progressTimer}, lookupTimer=${c.lookupTimer}`);
  }

  // 8. A card whose own category is "unknown" (a popular/model-list match
  // named the folder, not the workflow) must still reconnect to its running
  // download on a fresh controller - it only has the source's directory to
  // go on, and that must mirror what start() itself would have posted.
  {
    const progress = {
      dZ: { status: 'downloading', progress: 40, filename: 'u.safetensors', category: 'loras', canonical_category: 'loras' },
    };
    api.fetchApi = async (path) => reply(path === '/model_linker/progress' ? progress : {});
    const c = new DownloadController(fakeDialog());
    const cont = fakeContainer();
    const m = mk(1, 'u.safetensors', 'unknown', {
      download_source: { url: 'https://huggingface.co/a/b/resolve/main/u.safetensors', source: 'popular',
                          directory: 'loras', canonical_category: 'loras' },
    });
    c.afterRender(cont, { missing_models: [m] });
    await sleep(30);
    const html = cont.els.get(c.blockId(m))?.innerHTML || '';
    check('an unknown-category card reconnects to its record', /progress/.test(html), html.slice(0, 160));
    c.stop();
  }

  // 9. reconnect() must not adopt a download with no matching card on
  // screen - an earlier workflow's download, or another tab's, must not
  // keep this controller polling or later fire a false "Downloaded" toast.
  {
    const progress = { dW: { status: 'downloading', progress: 10, filename: 'unrelated.safetensors', category: 'loras' } };
    api.fetchApi = async (path) => reply(path === '/model_linker/progress' ? progress : {});
    const c = new DownloadController(fakeDialog());
    c.afterRender(fakeContainer(), { missing_models: [mk(1, 'z.safetensors', 'loras')] });
    await sleep(30);
    check('an unrelated running download is not adopted', c.byFile.size === 0, `byFile=${[...c.byFile.keys()]}`);
    check('and nothing keeps polling for it', c.progressTimer === null, `progressTimer=${c.progressTimer}`);
    c.stop();
  }

  // 10. "Search similar": results render as scored rows, a chosen candidate
  // downloads under its own filename (never the workflow's), its progress
  // shows on the card that started it without replacing that card's own
  // "not found" message, and "download all found" never touches it.
  {
    const candidates = [
      { source: 'huggingface', filename: 'qwen3vl_8b_bf16.safetensors',
        url: 'https://huggingface.co/a/b/resolve/main/qwen3vl_8b_bf16.safetensors', confidence: 95, different_model: false },
      { source: 'huggingface', filename: 'qwen3vl_4b_int8.safetensors',
        url: 'https://huggingface.co/a/b/resolve/main/qwen3vl_4b_int8.safetensors', confidence: 69, different_model: true },
    ];
    let searchBody;
    const progress = {};
    api.fetchApi = async (path, opts = {}) => {
      const body = opts.body ? JSON.parse(opts.body) : undefined;
      if (path === '/model_linker/search') { searchBody = body; return reply({ candidates, errors: [] }); }
      if (path === '/model_linker/download') {
        progress.sim1 = { status: 'downloading', progress: 20, filename: body.filename, category: body.category };
        return reply({ download_id: 'sim1' });
      }
      if (path === '/model_linker/progress') return reply(progress);
      return reply({});
    };
    const c = new DownloadController(fakeDialog());
    const cont = fakeContainer();
    const m = mk(1, 'qwen3vl_8b_int8.safetensors', 'checkpoints', { lookup_state: 'not_found', download_source: null });
    c.afterRender(cont, { missing_models: [m] });

    await c.searchSimilar(m);
    check("search similar posts the missing model's own path and category",
          searchBody?.missing?.original_path === 'qwen3vl_8b_int8.safetensors' && searchBody?.missing?.category === 'checkpoints');
    let html = cont.els.get(c.blockId(m))?.innerHTML || '';
    check('a different-size candidate is labelled in its own row',
          /qwen3vl_4b_int8\.safetensors[\s\S]*different model/.test(html));
    check('the card still shows its own "not found" message alongside the list',
          /No download found online/.test(html));

    await c.startSimilar(m, 0);
    html = cont.els.get(c.blockId(m))?.innerHTML || '';
    check("a started similar download saves under the candidate's own filename, never the workflow's",
          progress.sim1?.filename === 'qwen3vl_8b_bf16.safetensors', JSON.stringify(progress));
    check('its progress shows on the card that started it',
          /qwen3vl_8b_bf16\.safetensors[\s\S]*progress/.test(html), html);
    check("the card's own block is unaffected - still \"not found\", not the candidate's status",
          /No download found online/.test(html));

    await c.downloadAll();
    check('"download all found" never starts a similar candidate',
          Object.keys(progress).length === 1, JSON.stringify(progress));
    c.stop();
  }

  // 11. A failed search renders its error instead of throwing, and offers
  // nothing to download.
  {
    api.fetchApi = async (path) => (path === '/model_linker/search' ? reply({ error: 'HTTP 500' }, false, 500) : reply({}));
    const c = new DownloadController(fakeDialog());
    const cont = fakeContainer();
    const m = mk(1, 'x.safetensors', 'loras', { lookup_state: 'idle', download_source: null });
    c.afterRender(cont, { missing_models: [m] });
    await c.searchSimilar(m);
    const html = cont.els.get(c.blockId(m))?.innerHTML || '';
    check('a failed search shows its error, not a crash', /HTTP 500/.test(html));
    check('a failed search leaves no downloadable candidate', !/download-similar/.test(html));
    c.stop();
  }

  const similar = src.slice(src.indexOf('similarHtml(missing) {'), src.indexOf('async downloadAll'));
  check('similar results are escaped', /escapeHtml\(candidate\.filename\)/.test(similar));
  check('different models are labelled', /different model/.test(similar));
  check('a similar file keeps its own name',
        /filename:\s*candidate\.filename/.test(src.slice(src.indexOf('async startSimilar'))));
  check('download all never takes a similar result',
        !/similar/i.test(src.slice(src.indexOf('async downloadAll'), src.indexOf('stop() {'))));
  check('searching similar is offered when no exact download exists',
        /"similar"/.test(src.slice(src.indexOf('blockHtml(missing) {'), src.indexOf('refresh(missing) {'))));

  return check.failures;
}
