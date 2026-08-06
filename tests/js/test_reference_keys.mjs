// A model reference is identified by (node_id, widget_index, list_index,
// subgraph_id). Several loras share one node and one widget index, so ignoring
// list_index collides them: picking a replacement for one would target another.
import { createChecker, loadPrivates, readSource } from './harness.mjs';

export const name = 'reference identity';

export default async function run() {
  const check = createChecker(name);
  const { refSlot, refKey } = loadPrivates(['refSlot', 'refKey']);

  const lora = (listIndex) => ({ node_id: 7, widget_index: 1, list_index: listIndex,
                                 subgraph_id: null, is_top_level: true });

  check('loras sharing a node and widget get distinct slots',
        refSlot(lora(0)) !== refSlot(lora(1)),
        `${refSlot(lora(0))} vs ${refSlot(lora(1))}`);
  check('loras sharing a node and widget get distinct keys',
        refKey(lora(0)) !== refKey(lora(1)));

  const plain = { node_id: 3, widget_index: 0, subgraph_id: null, is_top_level: true };
  check('a reference with no list position still yields a slot', !!refSlot(plain));
  check('the same reference always yields the same key',
        refKey({ ...plain }) === refKey({ ...plain }));

  // The same node id exists both as a top-level subgraph instance and inside
  // the definition; only these fields tell them apart
  check('a subgraph node differs from the top-level node of the same id',
        refKey({ ...plain, subgraph_id: 'sub-1', is_top_level: false }) !== refKey(plain));
  check('a subgraph instance differs from the definition node',
        refKey({ ...plain, subgraph_id: 'sub-1', is_top_level: true })
        !== refKey({ ...plain, subgraph_id: 'sub-1', is_top_level: false }));

  check('a slot is usable as part of a CSS id',
        /^[\w-]+$/.test(String(refSlot(lora(1)))), String(refSlot(lora(1))));

  // Building these by hand is what caused the collisions in the first place
  const source = readSource();
  const handBuilt = source.match(
    /`[^`]*\$\{(?:missing|r|resolution)\.node_id\}[^`]*\$\{(?:missing|r|resolution)\.widget_index\}[^`]*`/g) || [];
  check('per-reference ids and keys all go through refSlot/refKey',
        handBuilt.length === 0, handBuilt.join(' | '));

  return check.failures;
}
