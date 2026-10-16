// Tokens are write-only from the browser's side: the dialog sends them and
// shows a hint, but never receives or renders the value.
import { createChecker, readSource } from './harness.mjs';

export const name = 'settings dialog';

export default async function run() {
  const check = createChecker(name);
  const src = readSource('modules/settings-dialog.js');
  check('token inputs are password fields', /type:\s*["']password["']/.test(src));
  check('posts to the settings route', /\/model_linker\/settings/.test(src));
  check('can check a token', /\/model_linker\/settings\/check\//.test(src));
  check('offers the background lookup switch', /auto_lookup/.test(src));
  check('explains what background lookup sends', /HuggingFace and CivitAI/.test(src));
  check('the header opens it', /new SettingsDialog\(/.test(readSource('modules/linker-dialog.js')));
  return check.failures;
}
