// Minimal test harness for web/linker.js.
//
// Node has no ComfyUI, so the module's two imports are answered by stubs. Two
// ways in, depending on what a test needs:
//
//   loadExtension()  - copies the live file next to the stubs and imports it,
//                      so `../../scripts/app.js` resolves, and returns whatever
//                      it passed to registerExtension
//   loadPrivates()   - evaluates the source with its imports stripped, to reach
//                      module-private helpers that are never exported
//
// Both read the file at its real location every run. An earlier version of this
// harness imported a copy, and went on reporting a setting as registered after
// it had been deleted.

import { copyFileSync, mkdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const HERE = dirname(fileURLToPath(import.meta.url));

export const LINKER_PATH = join(HERE, '..', '..', 'web', 'linker.js');

export function readSource() {
  return readFileSync(LINKER_PATH, 'utf8');
}

export async function loadExtension() {
  const target = join(HERE, 'stubs', 'extensions', 'comfyui-model-linker');
  mkdirSync(target, { recursive: true });
  const copy = join(target, 'linker.js');
  copyFileSync(LINKER_PATH, copy);
  globalThis.__registered = undefined;
  // Cache-busted so repeated runs in one process see the current file
  await import('file://' + copy.replace(/\\/g, '/') + '?' + Date.now());
  return globalThis.__registered;
}

export function loadPrivates(names) {
  const source = readSource().replace(/^import .*$/gm, '');
  const sandbox = {
    app: { registerExtension() {} },
    api: {},
    URL, console, document: undefined, window: undefined, localStorage: undefined,
  };
  return vm.runInNewContext(
    `${source}\n;({ ${names.join(', ')} })`, vm.createContext(sandbox));
}

export function createChecker(suiteName) {
  const failures = [];
  const check = (name, ok, detail = '') => {
    console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? '  ' + detail : ''}`);
    if (!ok) failures.push(`${suiteName}: ${name}`);
  };
  check.failures = failures;
  return check;
}
