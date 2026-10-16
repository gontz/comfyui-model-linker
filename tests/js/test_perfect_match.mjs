// A 100% match from a folder the node cannot load from is not "perfect":
// it must never be auto-linked or shown as one click from resolved.
import { createChecker, loadModule, readSource } from './harness.mjs';

export const name = 'perfect match';

export default async function run() {
  const check = createChecker(name);
  const { isPerfectMatch } = await loadModule('modules/util.js');

  check('exact match in the right folder is perfect',
        isPerfectMatch({ confidence: 100, category_mismatch: false }) === true);
  check('exact match in the wrong folder is not',
        isPerfectMatch({ confidence: 100, category_mismatch: true }) === false);
  check('a 99.9% match is not', isPerfectMatch({ confidence: 99.9 }) === false);
  check('missing match is not', isPerfectMatch(undefined) === false);

  const dialog = readSource('modules/linker-dialog.js');
  check('the dialog no longer tests confidence === 100 directly',
        !/confidence\s*===\s*100/.test(dialog));
  check('wrong-folder matches are labelled', /wrong folder/.test(dialog));
  return check.failures;
}
