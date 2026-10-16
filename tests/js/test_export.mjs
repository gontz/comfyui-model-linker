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
