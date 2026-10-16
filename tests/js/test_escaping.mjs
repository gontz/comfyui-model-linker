// Values interpolated into the dialog's HTML come from workflow files, which
// are untrusted: a workflow can be downloaded from anywhere and its model
// names and URLs are attacker-controlled text.
import { createChecker, loadModule, readSource } from './harness.mjs';

export const name = 'escaping untrusted workflow values';

export default async function run() {
  const check = createChecker(name);
  const { escapeHtml, safeHttpUrl } = await loadModule('modules/util.js');

  check('angle brackets are escaped',
        escapeHtml('<img src=x onerror=alert(1)>') === '&lt;img src=x onerror=alert(1)&gt;');
  check('quotes and ampersands are escaped',
        escapeHtml(`a&b"c'd`) === 'a&amp;b&quot;c&#39;d');
  check('an attribute cannot be broken out of',
        !escapeHtml('" onmouseover="steal()').includes('"'));
  check('null and undefined become empty text',
        escapeHtml(null) === '' && escapeHtml(undefined) === '');
  check('numbers survive', escapeHtml(42) === '42');

  check('https is accepted',
        safeHttpUrl('https://huggingface.co/a/b.safetensors')?.startsWith('https://'));
  check('http is accepted', safeHttpUrl('http://example.com/m.ckpt')?.startsWith('http://'));
  check('a javascript: URL is refused', safeHttpUrl('javascript:alert(document.cookie)') === null);
  check('a data: URL is refused',
        safeHttpUrl('data:text/html,<script>alert(1)</script>') === null);
  check('a file: URL is refused',
        safeHttpUrl('file:///C:/Windows/System32/notepad.exe') === null);
  check('malformed and empty input is refused',
        safeHttpUrl('not a url') === null && safeHttpUrl('') === null
        && safeHttpUrl(null) === null);
  check('a non-string that stringifies to a URL is refused',
        safeHttpUrl({ toString: () => 'https://evil.example' }) === null);

  // The missing-model card prints names taken from the workflow file
  {
    const src = readSource('modules/linker-dialog.js');
    const start = src.indexOf('renderMissingModel(missing) {');
    const end = src.indexOf('showNotification(message', start);
    const body = src.slice(start, end);
    const raw = [
      /\$\{missing\.subgraph_name\}/,
      /\$\{missing\.node_type\}/,
      /\$\{missing\.node_id\}/,
      /\$\{missing\.original_path\}/,
      /\$\{missing\.category \|\| 'unknown'\}/,
      /title="\$\{match\.model\?\.relative_path/,
      />\$\{label\}</,
      /\$\{folderPath\}/,
    ];
    check('renderMissingModel was located', start !== -1 && end > start);
    for (const pattern of raw) {
      check(`renderMissingModel escapes ${pattern}`, !pattern.test(body));
    }
  }

  // Element ids are built from workflow-supplied node and subgraph ids
  {
    const { refSlot } = await loadModule('modules/util.js');
    const slot = refSlot({ node_id: '1" onmouseover="x', widget_index: 0,
                           subgraph_id: '#a b:c<d>' });
    check('refSlot output is attribute- and selector-safe', /^[A-Za-z0-9_-]+$/.test(slot));
    check('refSlot keeps plain ids readable',
          refSlot({ node_id: 12, widget_index: 0 }) === '12-0-n-top');
  }

  return check.failures;
}
