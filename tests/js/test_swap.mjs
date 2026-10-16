// One model used by several nodes is one row, and swapping it queues a
// resolution for every one of those nodes.
import { createChecker, loadModule, readSource } from './harness.mjs';

export const name = 'swap';

export default async function run() {
  const check = createChecker(name);
  const { groupPresentModels } = await loadModule('modules/util.js');
  const ref = (id, path, category = 'loras', extra = {}) =>
    ({ node_id: id, widget_index: 0, original_path: path, category, canonical_category: category, ...extra });

  const groups = groupPresentModels([
    ref(1, 'a.safetensors'), ref(2, 'a.safetensors'), ref(3, 'b.safetensors'),
    ref(4, 'a.safetensors', 'clip', { canonical_category: 'text_encoders' }),
    ref(5, 'x.safetensors', 'loras', { list_index: 0 }), ref(5, 'x.safetensors', 'loras', { list_index: 1 }),
  ]);
  const a = groups.find(g => g.name === 'a.safetensors' && g.category === 'loras');
  check('same file and folder group together', a && a.refs.length === 2);
  check('the same name in another folder is a separate row',
        groups.filter(g => g.name === 'a.safetensors').length === 2);
  check('two list entries on one node stay two references',
        groups.find(g => g.name === 'x.safetensors').refs.length === 2);

  const src = readSource('modules/linker-dialog.js');
  check('the picker takes a selection callback', /attachModelCombo\(container, missing, onChoose/.test(src));
  check('the picker no longer queues directly', (src.match(/this\.queueResolution\(missing, chosen\)/g) || []).length === 0);
  check('a swap queues every referencing node',
        /group\.refs\.forEach\(\(ref\) => this\.queueResolution\(\{ \.\.\.ref, record_override: false \}, model\)\)/.test(src));
  check('removing a swap removes it from every node',
        /group\.refs\.forEach\(\(ref\) => this\.removeQueuedResolution\(ref\)\)/.test(src));
  check('a resolution says whether to remember it', /record_override: missing\.record_override !== false/.test(src));
  check('applying selections is one undo step',
        /updateWorkflowInComfyUI\(workflow, resolutions\) \{[\s\S]{0,1200}emitBeforeChange\?\.\(\)[\s\S]*?emitAfterChange\?\.\(\)/.test(src));
  return check.failures;
}
