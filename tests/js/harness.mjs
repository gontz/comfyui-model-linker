// Minimal test harness for the web/ frontend.
//
// Node has no ComfyUI, so the imports of scripts/app.js and scripts/api.js are
// answered by stubs. Three ways in, depending on what a test needs:
//
//   loadExtension() - copies the live web/ tree next to the stubs and imports
//                     the entry point, so its "../../scripts/app.js" resolves,
//                     and returns whatever it passed to registerExtension
//   loadModule()    - imports one module from that same copy, for the helpers
//                     it exports
//   readSource()    - the text of a module, for assertions about the code
//
// Everything reads web/ at its real location every run. An earlier version of
// this harness imported a stale copy and went on reporting a setting as
// registered after it had been deleted.

import { cpSync, mkdirSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));

export const WEB_DIR = join(HERE, '..', '..', 'web');

/** Text of a module, relative to web/ (default: the entry point). */
export function readSource(relative = 'linker.js') {
  return readFileSync(join(WEB_DIR, relative), 'utf8');
}

/**
 * Every .js file under web/, concatenated.
 *
 * Bans on `alert()`, on importing the deprecated ui.js and so on apply to the
 * whole frontend. Checking only the entry point would let any of them back in
 * through a module - which is exactly what splitting the file made possible.
 */
export function readAllSources() {
  const files = readdirSync(WEB_DIR, { recursive: true, withFileTypes: true })
    .filter(entry => entry.isFile() && entry.name.endsWith('.js'));
  return files
    .map(entry => `\n/* ==== ${entry.name} ==== */\n`
                  + readFileSync(join(entry.parentPath ?? entry.path, entry.name), 'utf8'))
    .join('\n');
}

// The copy is made once per process; the tree is small and every suite wants it
let stagedAt = null;

function stage() {
  if (stagedAt) return stagedAt;
  const target = join(HERE, 'stubs', 'extensions', 'comfyui-model-linker');
  mkdirSync(dirname(target), { recursive: true });
  // The whole tree: the entry point imports ./modules/, which import ../../scripts/
  cpSync(WEB_DIR, target, { recursive: true });
  stagedAt = target;
  return target;
}

const cacheBust = Date.now();

/** Import one module from the staged copy, e.g. "modules/util.js". */
export async function loadModule(relative) {
  const path = join(stage(), relative).replace(/\\/g, '/');
  return import('file://' + path + '?' + cacheBust);
}

export async function loadExtension() {
  globalThis.__registered = undefined;
  await loadModule('linker.js');
  return globalThis.__registered;
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
