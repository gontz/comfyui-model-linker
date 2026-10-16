// What the download block shows, and the rules that keep it cheap and safe:
// results patch cards in place, one download per file, nothing unescaped.
import { createChecker, loadModule, readSource } from './harness.mjs';

export const name = 'downloads';

export default async function run() {
  const check = createChecker(name);
  const { downloadState, isFinished } = await loadModule('modules/downloads.js');

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

  const dialog = readSource('modules/linker-dialog.js');
  check('closing the dialog stops polling', /close\(\)\s*\{[\s\S]{0,600}downloads\.stop\(\)/.test(dialog));
  check('the footer offers download all', /downloadAll\(\)/.test(dialog));
  return check.failures;
}
