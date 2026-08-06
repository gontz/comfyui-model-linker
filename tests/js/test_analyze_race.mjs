// Two analyses can overlap - reopening the dialog, or hitting Refresh, starts
// a second one while the first is still in flight. Whichever *arrives* last
// must not win; the newest request is the one describing the workflow now on
// screen.
import { createChecker, readSource } from './harness.mjs';

export const name = 'overlapping analyses';

export default async function run() {
  const check = createChecker(name);
  const source = readSource('modules/linker-dialog.js');

  // Bounded by the next method's doc comment, not by the first mention of
  // getCurrentWorkflow - the method calls that itself, well before the fetch.
  const start = source.indexOf('async loadWorkflowData');
  const end = source.indexOf('Get current workflow from ComfyUI', start);
  check('the method under test was located', start !== -1 && end > start);
  const body = source.slice(start, end);

  check('a new analysis abandons the one in flight',
        /this\.analyzeAbort\s*\.\s*abort\(\)/.test(body));
  check('the request carries the abort signal',
        /signal:\s*abort\.signal/.test(body));
  check('a superseded answer is discarded rather than rendered',
        /this\.analyzeAbort\s*!==\s*abort/.test(body));
  check('aborting is not reported to the user as an error',
        /AbortError/.test(body));
  check('closing the dialog abandons the request',
        /close\(\)\s*\{[\s\S]{0,400}?analyzeAbort[\s\S]{0,80}?abort\(\)/.test(source));

  // The behaviour itself, against a stub of the two calls it makes
  const calls = [];
  const dialog = {
    contentElement: { innerHTML: '' },
    analyzeAbort: null,
    getCurrentWorkflow: () => ({ nodes: [] }),
    displayMissingModels: (_el, data) => calls.push(data.tag),
    async fetchAnalysis(tag, delayMs, signal) {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, delayMs);
        signal.addEventListener('abort', () => {
          clearTimeout(timer);
          reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
        });
      });
      return { tag };
    },
  };

  // A faithful transcription of the method's control flow
  async function loadWorkflowData(tag, delayMs) {
    if (dialog.analyzeAbort) dialog.analyzeAbort.abort();
    const abort = new AbortController();
    dialog.analyzeAbort = abort;
    try {
      const data = await dialog.fetchAnalysis(tag, delayMs, abort.signal);
      if (dialog.analyzeAbort !== abort || !dialog.contentElement) return;
      dialog.displayMissingModels(dialog.contentElement, data);
    } catch (error) {
      if (error.name === 'AbortError' || dialog.analyzeAbort !== abort) return;
      throw error;
    } finally {
      if (dialog.analyzeAbort === abort) dialog.analyzeAbort = null;
    }
  }

  // The slow one is started first and would otherwise land last
  const slow = loadWorkflowData('stale', 60);
  const fresh = loadWorkflowData('fresh', 5);
  await Promise.all([slow, fresh]);
  await new Promise(resolve => setTimeout(resolve, 120));

  check('only the newest analysis is rendered',
        calls.length === 1 && calls[0] === 'fresh', `rendered: ${calls.join(', ') || 'nothing'}`);
  check('the in-flight marker is cleared when the last one settles',
        dialog.analyzeAbort === null);

  return check.failures;
}
