// Values interpolated into the dialog's HTML come from workflow files, which
// are untrusted: a workflow can be downloaded from anywhere and its model
// names and URLs are attacker-controlled text.
import { createChecker, loadModule } from './harness.mjs';

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

  return check.failures;
}
