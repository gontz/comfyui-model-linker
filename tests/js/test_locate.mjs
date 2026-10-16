// Locate must reach nodes inside (nested) subgraphs - entering the subgraph
// the way ComfyUI's own locate button does - rather than refusing.
import { createChecker, readSource } from './harness.mjs';

export const name = 'locate';

export default async function run() {
  const check = createChecker(name);
  const src = readSource('modules/linker-dialog.js');
  check('no disabled "Located in subgraph" button remains', !/Located in subgraph/.test(src));
  check('focusNode enters the node\'s own graph', /async focusNode\(node\)[\s\S]{0,700}setGraph\(/.test(src));
  check('focusNode animates like the frontend does', /animateToBounds/.test(src));
  check('locate works from a reference, not a bare id', /async locateRef\(ref\)/.test(src));
  check('a subgraph without a live instance is reported',
        /has no live instance/.test(src.slice(src.indexOf('async locateRef'))));
  check('one helper finds live nodes for a reference', /liveNodesFor\(ref\)\s*\{/.test(src));
  return check.failures;
}
