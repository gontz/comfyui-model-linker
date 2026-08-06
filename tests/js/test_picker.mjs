// What the replacement picker will show. Media files and sidecars are not
// replacements for a missing model, and offering one lets it be written into
// the workflow.
import { createChecker, loadPrivates } from './harness.mjs';

export const name = 'model picker filtering';

export default async function run() {
  const check = createChecker(name);
  const { isSelectableModel } = loadPrivates(['isSelectableModel']);

  const rejected = ['a.jpg', 'a.jpeg', 'a.png', 'a.webp', 'a.gif', 'a.bmp', 'a.avif',
                    'a.mp4', 'a.webm', 'a.mov', 'a.avi', 'a.mkv',
                    'x.civitai.info', 'x.json.lock', 'notes.md', 'page.html',
                    'data.csv', 'run.log'];
  const accepted = ['model.safetensors', 'm.ckpt', 'm.pt', 'm.pth', 'm.bin', 'm.gguf',
                    'm.onnx', 'm.sft', 'm.pkl',
                    // declared assets a category can legitimately register
                    'chat_template_qwen3.5-35b.jinja', 'fmt.json', 'prompt1.txt',
                    'look.cube', 'face.tflite', 'no_extension_at_all'];

  check('media and sidecar files are hidden',
        rejected.every(f => !isSelectableModel({ filename: f })),
        rejected.filter(f => isSelectableModel({ filename: f })).join(', ') || 'all hidden');
  check('real models and declared assets are shown',
        accepted.every(f => isSelectableModel({ filename: f })),
        accepted.filter(f => !isSelectableModel({ filename: f })).join(', ') || 'all shown');
  check('the check is case-insensitive', !isSelectableModel({ filename: 'PREVIEW.JPG' }));
  check('it falls back to relative_path',
        !isSelectableModel({ relative_path: 'sub/dir/prev.png' }));
  check('a malformed entry does not throw',
        isSelectableModel({}) === true && isSelectableModel(null) === true);

  // Scoping to the node's own category, on a catalogue shaped like a real one
  const catalogue = [
    { filename: 'qwen_3_8b.safetensors', canonical_category: 'text_encoders' },
    { filename: 'pid_qwenimage.metadata.json', canonical_category: 'checkpoints' },
    { filename: 'chat_template_qwen3.5-35b.jinja', canonical_category: 'checkpoints' },
    { filename: 'qwen_preview.jpg', canonical_category: 'text_encoders' },
    { filename: 'umt5_xxl_fp16.safetensors', canonical_category: 'text_encoders' },
    { filename: 'qwen_lut.cube', canonical_category: 'luts' },
  ];
  const scoped = catalogue.filter(isSelectableModel)
                          .filter(m => m.canonical_category === 'text_encoders');
  check('scoping to a category leaves only that folder\'s models',
        scoped.length === 2 && scoped.every(m => m.filename.endsWith('.safetensors')),
        scoped.map(m => m.filename).join(', '));

  const unscoped = catalogue.filter(isSelectableModel);
  check('unscoped still hides media but keeps declared assets',
        !unscoped.some(m => m.filename.endsWith('.jpg'))
        && unscoped.some(m => m.filename.endsWith('.jinja')),
        unscoped.map(m => m.filename).join(', '));

  return check.failures;
}
