// The dialog's own stylesheet forces a block layout on each missing-model card's
// children with !important. The per-card "Selected" bar hides itself with an
// inline display:none, which such a rule would override - every card then shows
// an empty green bar. Seen in the running app; pinned here.
import { createChecker, readSource } from './harness.mjs';

export const name = 'dialog css';

export default async function run() {
  const check = createChecker(name);
  const src = readSource('modules/linker-dialog.js');
  const rule = src.match(/#model-linker-missing-list > div > div[^,{]*/);
  check('the card-children rule was located', !!rule);
  check('the forced block layout skips the Selected bar',
        !!rule && rule[0].includes(':not(.model-linker-selected)'));
  return check.failures;
}
