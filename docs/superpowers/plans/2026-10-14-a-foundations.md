# Plan A — Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the small gaps against upstream (word-order matching, wrong-folder flag, routes off the event loop, stale introspection cache), fix an XSS hole in the missing-model card, and add the server-side settings store (API tokens + the `auto_lookup` switch) that Plan B's downloads and lookups need.

**Architecture:** Matching changes stay inside `core/matcher.py`'s banded scorer; the wrong-folder flag rides on the existing `same_category` computation in `find_matches`. Blocking route bodies move to `asyncio.to_thread`, so the scanner's module-level cache gets a lock. Settings are a JSON file under the extension's `data/` directory (gitignored), read and written by a new `core/settings.py` ported from upstream; its routes live in a new `core/routes_settings.py` so `__init__.py` stops growing.

**Tech Stack:** Python ≥3.9 stdlib, `requests` (declared, guarded), aiohttp routes on ComfyUI's `PromptServer`, ES-module frontend in `web/modules/`, tests via `python tests/run.py` (unittest + Node source checks).

**Spec:** `docs/superpowers/specs/2026-10-14-upstream-parity-design.md`

## Global Constraints

- Python floor: `requires-python = ">=3.9"` (was 3.8; `asyncio.to_thread` needs 3.9).
- No new hard dependency: `requests` is declared in `requirements.txt` and `pyproject.toml`, but any module importing it is imported inside a `try` in route registration so the extension loads without it.
- Upstream code is ported with `git show bec5c80:<path>` — never by adding a remote, merging or rebasing (CLAUDE.md, "This fork does not track upstream").
- Category names are never compared literally: use `canonical_category()` / `categories_match()` from `core/categories.py`.
- Escape every workflow-derived string interpolated into HTML with `escapeHtml()`; URLs go through `safeHttpUrl()`.
- Use `app.extensionManager.toast` (via `notifyError` or the dialog's `showNotification`), never `alert()`/`confirm()`.
- Never return a stored token to the browser; only whether one is set, its source and its last four characters.
- Tests must not touch the developer's real `data/` directory: patch `core.overrides._data_dir`, as `tests/test_overrides.py:34-38` does.
- Run the whole suite with `python tests/run.py` from the repo root; it must end green after every task.
- **Interpreter:** every `python` in this plan means ComfyUI's venv, `C:/ComfyUI/.venv/Scripts/python.exe` — the system `python` on this machine is a different install. Check once with `C:/ComfyUI/.venv/Scripts/python.exe -c "import requests, huggingface_hub"`.

## Review Focus

- A token saved with surrounding whitespace or a trailing newline (pasted from a web page) must still authenticate — `get_token` strips it. Pinned in Task 6.
- A settings file that is corrupt or not a JSON object must read as empty settings, not crash every route. Pinned in Task 6.
- Two analyses arriving concurrently on a cold cache must not both walk the whole model library and race on `_scan_cache`. Pinned in Task 5.
- A match from an aliased folder (`clip` vs `text_encoders`) must not be flagged "wrong folder". Pinned in Task 2.
- A workflow whose model name contains HTML (`<img src=x onerror=…>.safetensors`) must render as text in the missing-model card. Pinned in Task 3.

---

### Task 1: Word-order-independent filename similarity

**Files:**
- Modify: `core/matcher.py:219-269` (`calculate_filename_confidence`)
- Test: `tests/test_matcher.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: no signature change. `calculate_filename_confidence(target, candidate) -> float` (0–100) now treats reordered tokens as close.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_matcher.py`:

```python
class WordOrderTests(unittest.TestCase):
    """Token order carries no meaning in a model filename."""

    def test_reordered_tokens_score_as_a_strong_match(self):
        # The case that made upstream and Model-Resolver both add this
        self.assertGreaterEqual(
            calculate_filename_confidence('yue2_convrot_int8.safetensors',
                                          'yue2_3b_int8_convrot.safetensors'),
            90.0)

    def test_pure_reorder_never_reaches_100(self):
        score = calculate_filename_confidence('flux1_dev_fp8.safetensors', 'dev_flux1_fp8.safetensors')
        self.assertGreaterEqual(score, 94.0)
        self.assertLess(score, 100.0)

    def test_reorder_cannot_lift_a_capacity_conflict(self):
        # The acceptance case for every matcher change: 4B is not 8B
        self.assertLess(
            calculate_filename_confidence('qwen3vl_8b_int8.safetensors', 'int8_qwen3vl_4b.safetensors'),
            STRONG_MATCH_CONFIDENCE)

    def test_same_model_still_outranks_reordered_other_size(self):
        pool = candidates('int8_qwen3vl_4b.safetensors', 'qwen3vl_8b_bf16.safetensors')
        best = find_matches('qwen3vl_8b_int8.safetensors', pool)[0]
        self.assertEqual(best['filename'], 'qwen3vl_8b_bf16.safetensors')
```

- [ ] **Step 2: Run them to verify the first one fails**

Run: `python -m unittest discover -t . -s tests -p "test_matcher.py" -v`
Expected: `test_reordered_tokens_score_as_a_strong_match` FAILS (currently 72.7) and `test_pure_reorder_never_reaches_100` FAILS (`flux1_dev_fp8` and `dev_flux1_fp8` normalise to families `flux1 dev` and `dev flux1`, which the same-family test compares as strings, so the pair is capped at 93.9). The other two pass already and must keep passing.

- [ ] **Step 3: Implement**

In `core/matcher.py`, inside `calculate_filename_confidence`, replace the single line

```python
    similarity = calculate_similarity(target_norm, candidate_norm)
```

with

```python
    similarity = calculate_similarity(target_norm, candidate_norm)
    # Word order carries no meaning in a filename: `convrot_int8` and
    # `int8_convrot` name the same thing. This only raises raw similarity,
    # which orders candidates within a band; the family and capacity caps
    # below still decide which band a candidate lands in.
    similarity = max(similarity, calculate_similarity(
        ' '.join(sorted(target_norm.split())),
        ' '.join(sorted(candidate_norm.split())),
    ))
```

and, further down the same function, replace

```python
    if target_family == candidate_family:
```

with

```python
    # A reordered name is the same model, so the family comparison ignores order too
    if sorted(target_family.split()) == sorted(candidate_family.split()):
```

Verified before hand-off on a scratch copy: with both edits the four new tests score 91.9 / 95.9 / 69.0 / 8B-first, and all 173 existing Python tests still pass.

- [ ] **Step 4: Run the whole suite**

Run: `C:/ComfyUI/.venv/Scripts/python.exe tests/run.py`
Expected: all pass. Every pre-existing matcher test is a golden check — if one changes, stop and investigate rather than editing the expectation (`SequenceMatcher.ratio()` is not symmetric; the target stays first).

- [ ] **Step 5: Commit**

```bash
git add core/matcher.py tests/test_matcher.py
git commit -m "Score reordered filename tokens as close matches"
```

---

### Task 2: Flag matches that sit in a folder the node cannot load from

**Files:**
- Modify: `core/matcher.py` (`find_matches` match dict, imports at line 23)
- Modify: `web/modules/util.js` (new export `isPerfectMatch`)
- Modify: `web/modules/linker-dialog.js:1165-1166, 1197, 1222-1223, 1337, 1363-1364, 1379, 1709`
- Test: `tests/test_matcher.py`, `tests/js/test_perfect_match.mjs` (new)

**Interfaces:**
- Consumes: `canonical_category(category) -> Optional[str]` and `categories_match(a, b) -> bool` from `core/categories.py`.
- Produces: every match dict from `find_matches` gains `'category_mismatch': bool` — True only when the expected category is known, the candidate's category is known, and they are not aliases of each other. Frontend export `isPerfectMatch(match) -> boolean` = `confidence === 100 && !category_mismatch`; Plan B's auto-resolve after download and Plan C reuse it.

The analyzer already reports a file in the wrong folder as missing (it resolves only within the node's own category), so this task is purely about the suggestions: a 100% match from another folder must not look one click from resolved, and must never be auto-linked.

- [ ] **Step 1: Write the failing Python tests**

Append to `tests/test_matcher.py`:

```python
class CategoryMismatchTests(unittest.TestCase):
    def test_match_from_another_folder_is_flagged(self):
        pool = candidates('flux1_dev.safetensors', category='vae')
        match = find_matches('flux1_dev.safetensors', pool, preferred_category='checkpoints')[0]
        self.assertTrue(match['category_mismatch'])

    def test_aliased_folder_is_not_flagged(self):
        # models/clip and models/text_encoders are one folder on most installs
        pool = candidates('t5xxl_fp16.safetensors', category='clip')
        match = find_matches('t5xxl_fp16.safetensors', pool, preferred_category='text_encoders')[0]
        self.assertFalse(match['category_mismatch'])

    def test_unknown_expected_category_flags_nothing(self):
        pool = candidates('a.safetensors', category='vae')
        for expected in (None, 'unknown', ''):
            match = find_matches('a.safetensors', pool, preferred_category=expected)[0]
            self.assertFalse(match['category_mismatch'], expected)

    def test_candidate_without_category_is_not_flagged(self):
        pool = [{'filename': 'a.safetensors', 'path': '/m/a.safetensors'}]
        match = find_matches('a.safetensors', pool, preferred_category='checkpoints')[0]
        self.assertFalse(match['category_mismatch'])
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m unittest discover -t . -s tests -p "test_matcher.py" -v`
Expected: FAIL with `KeyError: 'category_mismatch'`.

- [ ] **Step 3: Implement the backend flag**

In `core/matcher.py` change the import line

```python
from .categories import categories_match
```

to

```python
from .categories import canonical_category, categories_match
```

In `find_matches`, before the `for candidate in candidate_models:` loop, add:

```python
    # Only a known expected category can make a match "wrong folder"
    expected_known = canonical_category(preferred_category) is not None
```

and replace the `match = {...}` literal with:

```python
        same_category = categories_match(preferred_category, candidate.get('category'))
        match = {
            'model': candidate,
            'filename': candidate_filename,
            'similarity': similarity,
            'confidence': confidence,
            # Compared canonically, so a node expecting `text_encoders` still
            # counts a model catalogued under `clip` as its own category.
            'same_category': same_category,
            # The node resolves paths against its own category's folders only,
            # so linking this would still fail at prompt time.
            'category_mismatch': expected_known and bool(candidate.get('category')) and not same_category,
        }
```

- [ ] **Step 4: Run the Python tests**

Run: `python -m unittest discover -t . -s tests -p "test_matcher.py" -v`
Expected: PASS.

- [ ] **Step 5: Write the failing frontend test**

Create `tests/js/test_perfect_match.mjs`:

```js
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
```

- [ ] **Step 6: Run to verify it fails**

Run: `node tests/js/run.mjs`
Expected: FAIL — `isPerfectMatch` is not exported.

- [ ] **Step 7: Implement the frontend**

Append to `web/modules/util.js`:

```js
/**
 * A match that can be linked without a second look: exact by name and from a
 * folder the node actually loads from. A 100% match in another category would
 * leave the workflow failing at prompt time, so it is never auto-linked.
 */
export function isPerfectMatch(match) {
    return match?.confidence === 100 && !match?.category_mismatch;
}
```

In `web/modules/linker-dialog.js`, add `isPerfectMatch` to the existing `import { ... } from "./util.js";` list, then make these replacements (line numbers as of commit `8fa7da9`; locate by the quoted text):

| Where | Old | New |
|---|---|---|
| 1165–1166 | `aFiltered.some(m => m.confidence === 100)` / `bFiltered.some(m => m.confidence === 100)` | `aFiltered.some(isPerfectMatch)` / `bFiltered.some(isPerfectMatch)` |
| 1197 and 1337 | `filteredMatches.filter(m => m.confidence === 100)` | `filteredMatches.filter(isPerfectMatch)` |
| 1198 and 1338 | `filteredMatches.filter(m => m.confidence < 100 && m.confidence >= 70)` | `filteredMatches.filter(m => !isPerfectMatch(m))` |
| 1222–1223 and 1363–1364 | `if (a.confidence === 100 && b.confidence !== 100) return -1;` / `if (a.confidence !== 100 && b.confidence === 100) return 1;` | `if (isPerfectMatch(a) && !isPerfectMatch(b)) return -1;` / `if (!isPerfectMatch(a) && isPerfectMatch(b)) return 1;` |
| 1379 | `${match.confidence === 100 ? 'green' : 'orange'}` | `${isPerfectMatch(match) ? 'green' : 'orange'}` |
| 1709 | `matches.find((m) => m.confidence === 100)` | `matches.find(isPerfectMatch)` |

Directly after the confidence `<span>` at line 1379–1380, add:

```js
                if (match.category_mismatch) {
                    const foundIn = escapeHtml(match.model?.category || 'another folder');
                    const expected = escapeHtml(missing.category || 'its own folder');
                    html += ` <span style="color:#e0a030; font-weight:600;" `
                        + `title="Found in '${foundIn}', but this node loads from '${expected}'. Move the file there, or link it knowing the node may fail to load it.">⚠ wrong folder</span>`;
                }
```

- [ ] **Step 8: Run the whole suite**

Run: `python tests/run.py`
Expected: all pass, including `perfect match`.

- [ ] **Step 9: Verify in the app**

Restart ComfyUI (Python changed). Copy any LoRA file into `models/vae/` under a new name, load a workflow whose `LoraLoader` references that name. Open Model Linker (Alt+L): the match shows `⚠ wrong folder`, the card is not green, and "Auto-Resolve 100% Matches" reports nothing to resolve. Delete the copied file afterwards.

- [ ] **Step 10: Commit**

```bash
git add core/matcher.py web/modules/util.js web/modules/linker-dialog.js tests/test_matcher.py tests/js/test_perfect_match.mjs
git commit -m "Flag suggestions from folders the node cannot load from"
```

---

### Task 3: Escape workflow text in the missing-model card

**Files:**
- Modify: `web/modules/linker-dialog.js` (`renderMissingModel`, lines 1267–1430)
- Test: `tests/js/test_escaping.mjs`

**Interfaces:**
- Consumes: `escapeHtml(value) -> string` from `web/modules/util.js`.
- Produces: nothing new.

`renderMissingModel` interpolates `missing.subgraph_name`, `missing.node_type`, `missing.original_path`, `missing.category`, `match.model?.relative_path`, `label` and `folderPath` raw. All come from the workflow file or the filesystem, and the dialog opens automatically on load in Plan C — so a shared workflow could run script in the page.

- [ ] **Step 1: Write the failing test**

Append inside the default export of `tests/js/test_escaping.mjs`, before its `return`:

```js
  // The missing-model card prints names taken from the workflow file
  {
    const src = readSource('modules/linker-dialog.js');
    const start = src.indexOf('renderMissingModel(missing) {');
    const end = src.indexOf('showNotification(message', start);
    const body = src.slice(start, end);
    const raw = [
      /\$\{missing\.subgraph_name\}/,
      /\$\{missing\.node_type\}/,
      /\$\{missing\.original_path\}/,
      /\$\{missing\.category \|\| 'unknown'\}/,
      /title="\$\{match\.model\?\.relative_path/,
      />\$\{label\}</,
      /\$\{folderPath\}/,
    ];
    check('renderMissingModel was located', start !== -1 && end > start);
    for (const pattern of raw) {
      check(`renderMissingModel escapes ${pattern}`, !pattern.test(body));
    }
  }
```

(If `readSource` is not yet imported in that file, add it to the `import { ... } from './harness.mjs'` line.)

- [ ] **Step 2: Run to verify it fails**

Run: `node tests/js/run.mjs`
Expected: seven FAILs under `renderMissingModel escapes`.

- [ ] **Step 3: Implement**

At the top of `renderMissingModel(missing)` add:

```js
        // Everything below except our own markup comes from the workflow file
        const subgraphName = escapeHtml(missing.subgraph_name);
        const nodeType = escapeHtml(missing.node_type);
        const originalPath = escapeHtml(missing.original_path);
        const categoryText = escapeHtml(missing.category || 'unknown');
```

Then replace:
- `${missing.subgraph_name}` → `${subgraphName}`
- `${missing.node_type}` → `${nodeType}`
- `<code>${missing.original_path}</code>` → `<code>${originalPath}</code>`
- `${missing.category || 'unknown'}` → `${categoryText}`
- `` html += `<code title="${match.model?.relative_path || label}">${label}</code> `; `` →
  `` html += `<code title="${escapeHtml(match.model?.relative_path || label)}">${escapeHtml(label)}</code> `; ``
- `` title="${folderPath}">📁 ${folderPath}</div>` `` → `` title="${escapeHtml(folderPath)}">📁 ${escapeHtml(folderPath)}</div>` ``

- [ ] **Step 4: Run the whole suite**

Run: `python tests/run.py`
Expected: all pass.

- [ ] **Step 5: Verify in the app**

Save a copy of any workflow with a `CheckpointLoaderSimple` whose `ckpt_name` is `<img src=x onerror=alert(1)>.safetensors`, load it, open Model Linker: the name prints literally and no dialog appears.

- [ ] **Step 6: Commit**

```bash
git add web/modules/linker-dialog.js tests/js/test_escaping.mjs
git commit -m "Escape workflow names in the missing-model card"
```

---

### Task 4: Retry introspection for node types registered after the first analysis

**Files:**
- Modify: `core/node_schema.py:173-186` (`get_node_schema`)
- Test: `tests/test_node_schema.py` (new)

**Interfaces:**
- Consumes: `_get_node_class(node_type)` (`core/node_schema.py:39`).
- Produces: no signature change. A `None` result is cached only when the node class exists (introspection genuinely failed); an unregistered type is asked again next time.

- [ ] **Step 1: Write the failing test**

Create `tests/test_node_schema.py`:

```python
"""
Introspection results are cached per node type - but "this type does not exist
yet" is not a result. Custom nodes can register after the first analysis.
"""

import sys
import types
import unittest

import fake_folder_paths
from core import node_schema


class LateRegistrationTests(unittest.TestCase):
    def setUp(self):
        node_schema.clear_cache()
        self._saved_nodes = sys.modules.get('nodes')
        self.nodes = types.ModuleType('nodes')
        self.nodes.NODE_CLASS_MAPPINGS = {}
        sys.modules['nodes'] = self.nodes
        fake_folder_paths.reset()
        fake_folder_paths.add_category('loras', [], {'.safetensors'})

    def tearDown(self):
        node_schema.clear_cache()
        if self._saved_nodes is None:
            sys.modules.pop('nodes', None)
        else:
            sys.modules['nodes'] = self._saved_nodes
        fake_folder_paths.reset()

    def test_type_registered_later_is_introspected(self):
        self.assertIsNone(node_schema.get_node_schema('LateLoraLoader'))

        class LateLoraLoader:
            @classmethod
            def INPUT_TYPES(cls):
                import folder_paths
                return {'required': {'lora_name': (folder_paths.get_filename_list('loras'),)}}

        self.nodes.NODE_CLASS_MAPPINGS['LateLoraLoader'] = LateLoraLoader
        schema = node_schema.get_node_schema('LateLoraLoader')
        self.assertIsNotNone(schema)
        self.assertIn('loras', schema['categories'])
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest discover -t . -s tests -p "test_node_schema.py" -v`
Expected: FAIL — the second call returns the cached `None`.

- [ ] **Step 3: Implement**

In `core/node_schema.py`, replace the tail of `get_node_schema`:

```python
    schema = _introspect(node_type)
    _SCHEMA_CACHE[node_type] = schema
    return schema
```

with:

```python
    schema = _introspect(node_type)
    # A type that is not registered yet may be by the next analysis - custom
    # nodes keep registering after startup. Only a real answer, or a failure
    # for a class that exists, is worth remembering.
    if schema is not None or _get_node_class(node_type) is not None:
        _SCHEMA_CACHE[node_type] = schema
    return schema
```

- [ ] **Step 4: Run the whole suite**

Run: `python tests/run.py`
Expected: all pass. If `schema['categories']` has a different shape than a list of names, read `_introspect`'s return statement (`core/node_schema.py:170`) and adjust only the assertion.

- [ ] **Step 5: Commit**

```bash
git add core/node_schema.py tests/test_node_schema.py
git commit -m "Retry introspection for node types that register late"
```

---

### Task 5: Keep blocking work off ComfyUI's event loop

**Files:**
- Modify: `__init__.py` (imports; `/analyze` at ~line 85, `/resolve` at ~line 131, `/models` at ~line 210)
- Modify: `core/scanner.py:7-9` (imports), `:241` (lock), `:304-336` (`get_model_files`)
- Modify: `pyproject.toml:8`
- Test: `tests/test_scanner.py`, `tests/js/` not affected

**Interfaces:**
- Consumes: nothing new.
- Produces: `get_model_files()` is safe to call from several threads at once; concurrent cold calls perform one scan.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scanner.py` (it already imports `LibraryTestCase`):

```python
class ConcurrentScanTests(LibraryTestCase):
    def test_concurrent_cold_calls_scan_once(self):
        import threading
        from core import scanner

        self.add_models('checkpoints', ['a.safetensors'], ['.safetensors'])
        calls = []
        real = scanner.scan_all_directories

        def slow_scan(visited_dirs=None):
            calls.append(1)
            threading.Event().wait(0.2)  # long enough for the other thread to arrive
            return real(visited_dirs)

        scanner.scan_all_directories = slow_scan
        try:
            threads = [threading.Thread(target=scanner.get_model_files) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            scanner.scan_all_directories = real
        self.assertEqual(len(calls), 1)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest discover -t . -s tests -p "test_scanner.py" -v`
Expected: FAIL — `3 != 1`.

- [ ] **Step 3: Implement the lock**

In `core/scanner.py` add `import threading` to the imports, and under the `_scan_cache` declaration (line 241) add:

```python
# Routes run analyses in worker threads, so two can arrive on a cold cache at
# once. One walks the library; the other waits for its answer.
_scan_lock = threading.Lock()
```

Rewrite the body of `get_model_files` (keep its docstring):

```python
    global _scan_cache

    with _scan_lock:
        if not force_rescan and _scan_cache is not None:
            try:
                if _cache_is_valid(_scan_cache):
                    return _scan_cache['models']
            except Exception as e:
                logging.debug(f"Model Linker: cache validation failed, rescanning: {e}")

        visited_dirs: List[str] = []
        models = scan_all_directories(visited_dirs)

        _scan_cache = {
            'models': models,
            'dirs': _directory_signature(visited_dirs),
            'layout': _layout_signature(),
        }
        logging.debug(f"Model Linker: scanned {len(models)} models across {len(visited_dirs)} directories")
        return models
```

Also wrap `invalidate_cache`'s assignment: `with _scan_lock: _scan_cache = None`.

- [ ] **Step 4: Run the scanner tests**

Run: `python -m unittest discover -t . -s tests -p "test_scanner.py" -v`
Expected: PASS.

- [ ] **Step 5: Move the route bodies to threads**

In `__init__.py` add `import asyncio` next to `import json`. Then:

- `/analyze`: `result = analyze_and_find_matches(workflow_json)` → `result = await asyncio.to_thread(analyze_and_find_matches, workflow_json)`
- `/resolve`: `updated_workflow = apply_resolution(workflow_json, resolutions)` → `updated_workflow = await asyncio.to_thread(apply_resolution, workflow_json, resolutions)`
- `/models`: `models = list_available_models()` → `models = await asyncio.to_thread(list_available_models)`

In `pyproject.toml` change `requires-python = ">=3.8"` to `requires-python = ">=3.9"`.

- [ ] **Step 6: Run the whole suite**

Run: `python tests/run.py`
Expected: all pass.

- [ ] **Step 7: Verify in the app**

Restart ComfyUI. Load a workflow with missing models and open Model Linker; while the first (cold) analysis runs, queue a prompt — the queue request must answer immediately instead of waiting for the scan.

- [ ] **Step 8: Commit**

```bash
git add __init__.py core/scanner.py pyproject.toml tests/test_scanner.py
git commit -m "Run analysis, resolution and listing off the event loop"
```

---

### Task 6: Settings store — API tokens and the auto-lookup switch

**Files:**
- Create: `core/settings.py` (ported from `bec5c80:core/settings.py`, then edited)
- Create: `core/routes_settings.py`
- Create: `web/modules/settings-dialog.js`
- Modify: `__init__.py` (register the settings routes), `web/modules/linker-dialog.js` (header button), `requirements.txt`, `pyproject.toml`
- Test: `tests/test_settings.py` (new), `tests/js/test_settings_dialog.mjs` (new)

**Interfaces:**
- Consumes: `core.overrides._data_dir() -> str`.
- Produces (Plan B depends on all of these):
  - `settings.get_token(provider: str) -> Optional[str]` — `'huggingface'` or `'civitai'`; saved value first, then env var `HF_TOKEN` / `CIVITAI_API_TOKEN`; stripped.
  - `settings.provider_for_url(url: str) -> Optional[str]` — `'huggingface'` | `'civitai'` | None, by exact host or subdomain, https only.
  - `settings.auth_headers_for_url(url: str) -> Dict[str, str]`
  - `settings.get_option(name: str) -> Any` — booleans in `OPTION_DEFAULTS = {'auto_lookup': True}`.
  - `settings.save_settings(updates: dict) -> None`; `settings.public_view() -> dict`; `settings.check_token(provider) -> {'ok': bool, 'user'?: str, 'error'?: str}`.
  - Routes: `GET /model_linker/settings` → `public_view()`; `POST /model_linker/settings` body `{hf_token?, civitai_key?, auto_lookup?}` → `public_view()`; `POST /model_linker/settings/check/{provider}` → `check_token` result.
  - Frontend: `SettingsDialog` class exported from `web/modules/settings-dialog.js` with `show()`.

- [ ] **Step 1: Port the module**

```bash
git show bec5c80:core/settings.py > core/settings.py
```

(Fresh clone without the object: `gh api "repos/kianxyzw/comfyui-model-linker/contents/core/settings.py?ref=bec5c80" -H "Accept: application/vnd.github.raw" > core/settings.py`.)

- [ ] **Step 2: Write the failing tests**

Create `tests/test_settings.py`:

```python
"""Server-side settings: tokens never leave the server; options have defaults."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from core import overrides, settings


class SettingsTestCase(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix='model-linker-settings-')
        self._real = overrides._data_dir
        overrides._data_dir = lambda: self.data_dir
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop('HF_TOKEN', None)
        os.environ.pop('CIVITAI_API_TOKEN', None)

    def tearDown(self):
        self._env.stop()
        overrides._data_dir = self._real
        shutil.rmtree(self.data_dir, ignore_errors=True)


class TokenTests(SettingsTestCase):
    def test_saved_token_is_used_but_never_shown(self):
        settings.save_settings({'hf_token': 'hf_abcdefghijklmnop'})
        self.assertEqual(settings.get_token('huggingface'), 'hf_abcdefghijklmnop')
        view = settings.public_view()['huggingface']
        self.assertTrue(view['set'])
        self.assertEqual(view['hint'], '…mnop')
        self.assertNotIn('hf_abcdefghijklmnop', json.dumps(settings.public_view()))

    def test_pasted_whitespace_is_stripped(self):
        settings.save_settings({'civitai_key': '  abcdefghij\n'})
        self.assertEqual(settings.get_token('civitai'), 'abcdefghij')

    def test_empty_string_clears(self):
        settings.save_settings({'hf_token': 'hf_abcdefghijklmnop'})
        settings.save_settings({'hf_token': ''})
        self.assertIsNone(settings.get_token('huggingface'))

    def test_environment_is_the_fallback(self):
        os.environ['HF_TOKEN'] = 'hf_fromenvironment'
        self.assertEqual(settings.get_token('huggingface'), 'hf_fromenvironment')
        self.assertEqual(settings.public_view()['huggingface']['source'], 'env')

    def test_file_lives_in_the_data_directory(self):
        settings.save_settings({'hf_token': 'x' * 12})
        self.assertTrue(os.path.isfile(os.path.join(self.data_dir, 'settings.json')))

    def test_corrupt_file_reads_as_empty(self):
        with open(os.path.join(self.data_dir, 'settings.json'), 'w', encoding='utf-8') as f:
            f.write('[1, 2')
        self.assertEqual(settings.load_settings(), {})
        with open(os.path.join(self.data_dir, 'settings.json'), 'w', encoding='utf-8') as f:
            f.write('[1, 2]')
        self.assertEqual(settings.load_settings(), {})


class HostTests(SettingsTestCase):
    def test_only_real_hosts_receive_tokens(self):
        settings.save_settings({'hf_token': 'hf_abcdefghijklmnop', 'civitai_key': 'civitaikey123'})
        for url in ('https://evil.example/huggingface.co/x', 'https://huggingface.co.evil.example/x',
                    'https://evil.example/?u=civitai.com', 'https://notcivitai.com/x',
                    'http://huggingface.co/x'):
            self.assertEqual(settings.auth_headers_for_url(url), {}, url)
        for url in ('https://huggingface.co/a/b/resolve/main/c', 'https://cdn-lfs.huggingface.co/x',
                    'https://hf.co/x', 'https://civitai.com/api/download/models/1'):
            self.assertIn('Authorization', settings.auth_headers_for_url(url), url)


class OptionTests(SettingsTestCase):
    def test_auto_lookup_defaults_on(self):
        self.assertIs(settings.get_option('auto_lookup'), True)

    def test_auto_lookup_can_be_switched_off(self):
        settings.save_settings({'auto_lookup': False})
        self.assertIs(settings.get_option('auto_lookup'), False)
        self.assertIs(settings.public_view()['options']['auto_lookup'], False)

    def test_unknown_option_is_rejected(self):
        with self.assertRaises(KeyError):
            settings.get_option('no_such_option')
```

- [ ] **Step 3: Run to verify they fail**

Run: `python -m unittest discover -t . -s tests -p "test_settings.py" -v`
Expected: FAIL — the file goes to `folder_paths.get_user_directory()`, `get_option` does not exist, and `save_settings({'auto_lookup': False})` deletes nothing but `public_view` has no `options`.

- [ ] **Step 4: Edit the ported module**

In `core/settings.py`:

Replace the module docstring's first paragraph with:

```python
"""
Server-side settings: API tokens for HuggingFace and CivitAI, and options that
govern work the server does on its own (background lookups).

Tokens never go back to the browser: the UI only sees whether one is set, where
it came from, and its last four characters.
"""
```

Replace `SETTINGS_FILENAME = 'model_linker_settings.json'` and `get_settings_path` with:

```python
from . import overrides

SETTINGS_FILENAME = 'settings.json'

# Options the user can switch. Booleans only; anything else is rejected.
OPTION_DEFAULTS = {
    # Look up download sources for missing models in the background after each
    # analysis. Sends model filenames (and hashes) to HuggingFace and CivitAI.
    'auto_lookup': True,
}


def get_settings_path() -> str:
    """Beside the saved overrides, in the extension's gitignored data folder."""
    return os.path.join(overrides._data_dir(), SETTINGS_FILENAME)
```

(`overrides._data_dir` is called through the module so tests can redirect it.)

In `load_settings`, replace `except Exception as e:` with `except (OSError, ValueError) as e:` — a non-dict already returns `{}`.

In `save_settings`, replace the loop body so booleans survive (`False` is not "empty"):

```python
        for key, value in updates.items():
            if value is None or value == '':
                settings.pop(key, None)
            else:
                settings[key] = value
```

Add after `get_token`:

```python
def get_option(name: str) -> Any:
    """An option's saved value, or its default. Unknown names raise KeyError."""
    default = OPTION_DEFAULTS[name]
    value = load_settings().get(name, default)
    return value if isinstance(value, bool) else default
```

At the end of `public_view`, before `return view`, add:

```python
    view['options'] = {name: get_option(name) for name in OPTION_DEFAULTS}
```

Move `import requests` from the top of the file into `check_token` (first line of its body), so `get_token`/`provider_for_url` work without `requests` installed.

- [ ] **Step 5: Run the settings tests**

Run: `python -m unittest discover -t . -s tests -p "test_settings.py" -v`
Expected: PASS.

- [ ] **Step 6: Add the routes**

Create `core/routes_settings.py`:

```python
"""Routes for the settings store. Registered from __init__.setup_routes."""

import asyncio
import logging

from . import settings

logger = logging.getLogger(__name__)

# What the browser may write. Tokens are strings; options are booleans.
_TOKEN_KEYS = {'hf_token', 'civitai_key'}


def register(routes, web) -> None:
    @routes.get("/model_linker/settings")
    async def get_settings(request):
        return web.json_response(settings.public_view())

    @routes.post("/model_linker/settings")
    async def post_settings(request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'error': 'Body must be JSON'}, status=400)
        if not isinstance(data, dict):
            return web.json_response({'error': 'Body must be an object'}, status=400)

        updates = {}
        for key, value in data.items():
            if key in _TOKEN_KEYS and isinstance(value, str):
                updates[key] = value.strip()
            elif key in settings.OPTION_DEFAULTS and isinstance(value, bool):
                updates[key] = value
            else:
                return web.json_response({'error': f'Unsupported setting: {key}'}, status=400)
        settings.save_settings(updates)
        return web.json_response(settings.public_view())

    @routes.post("/model_linker/settings/check/{provider}")
    async def check(request):
        provider = request.match_info['provider']
        if provider not in settings.PROVIDERS:
            return web.json_response({'ok': False, 'error': 'Unknown provider'}, status=404)
        try:
            result = await asyncio.to_thread(settings.check_token, provider)
        except ImportError:
            result = {'ok': False, 'error': 'The requests package is not installed'}
        return web.json_response(result)
```

In `__init__.py`, inside `setup_routes`, directly before `self.routes_setup = True`, add:

```python
            from .core import routes_settings
            routes_settings.register(routes, web)
```

Add `requests>=2.28.0` as a line in `requirements.txt` and to `pyproject.toml`'s `dependencies` list.

- [ ] **Step 7: Write the failing frontend test**

Create `tests/js/test_settings_dialog.mjs`:

```js
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
```

- [ ] **Step 8: Run to verify it fails**

Run: `node tests/js/run.mjs`
Expected: FAIL — `modules/settings-dialog.js` does not exist.

- [ ] **Step 9: Implement the dialog**

Create `web/modules/settings-dialog.js`:

```js
/** Settings: API tokens (stored on the server) and background-lookup switch. */

import { api } from "../../../scripts/api.js";

import { $el } from "./dom.js";
import { notifyError } from "./util.js";

const PROVIDERS = [
    { id: "huggingface", key: "hf_token", label: "HuggingFace token",
      help: "A Read token from huggingface.co/settings/tokens. Needed for gated models." },
    { id: "civitai", key: "civitai_key", label: "CivitAI API key",
      help: "From civitai.com/user/account. Needed for login-only downloads." },
];

export class SettingsDialog {
    constructor() {
        this.rows = {};
        this.element = $el("div.comfy-modal", {
            id: "model-linker-settings-modal",
            parent: document.body,
            style: {
                position: "fixed", top: "50%", left: "50%", transform: "translate(-50%, -50%)",
                width: "560px", maxWidth: "95vw", maxHeight: "90vh", overflowY: "auto",
                backgroundColor: "var(--comfy-menu-bg, #202020)", color: "var(--input-text, #fff)",
                border: "2px solid var(--border-color, #555)", borderRadius: "8px",
                padding: "16px", zIndex: "100000", display: "none", flexDirection: "column", gap: "14px",
            },
        }, [
            $el("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } }, [
                $el("strong", { textContent: "Model Linker settings" }),
                $el("button", { textContent: "×", onclick: () => this.close(),
                    style: { background: "none", border: "none", color: "inherit", fontSize: "20px", cursor: "pointer" } }),
            ]),
            ...PROVIDERS.map((p) => this.providerRow(p)),
            this.lookupRow(),
        ]);
    }

    providerRow(provider) {
        const input = $el("input", { type: "password", autocomplete: "off",
            style: { flex: "1", minWidth: "0" } });
        const status = $el("div", { style: { fontSize: "12px", opacity: "0.8" } });
        const button = (text, onclick) => $el("button.model-linker-resolve-btn",
            { textContent: text, onclick, style: { padding: "2px 10px" } });
        const save = button("Save", () => this.save({ [provider.key]: input.value }, provider.id));
        const checkBtn = button("Check", () => this.check(provider.id));
        const clear = button("Clear", () => this.save({ [provider.key]: "" }));
        this.rows[provider.id] = { input, status, checkBtn, clear };
        return $el("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } }, [
            $el("label", { textContent: provider.label, style: { fontWeight: "600" } }),
            $el("div", { style: { display: "flex", gap: "6px" } }, [input, save, checkBtn, clear]),
            $el("div", { textContent: provider.help, style: { fontSize: "12px", opacity: "0.6" } }),
            status,
        ]);
    }

    lookupRow() {
        this.autoLookup = $el("input", { type: "checkbox",
            onchange: () => this.save({ auto_lookup: this.autoLookup.checked }) });
        return $el("div", { style: { display: "flex", flexDirection: "column", gap: "4px",
                borderTop: "1px solid var(--border-color)", paddingTop: "12px" } }, [
            $el("label", { style: { display: "flex", gap: "8px", alignItems: "center", fontWeight: "600" } }, [
                this.autoLookup, document.createTextNode("Find downloads for missing models in the background"),
            ]),
            $el("div", { style: { fontSize: "12px", opacity: "0.6" },
                textContent: "After each analysis, missing model filenames (and hashes, when the workflow "
                    + "records them) are looked up on HuggingFace and CivitAI. Results are kept, so they "
                    + "appear even if you close this window. Turn this off to keep filenames on this machine." }),
        ]);
    }

    render(view) {
        for (const p of PROVIDERS) {
            const row = this.rows[p.id];
            const v = view?.[p.id] || {};
            row.input.value = "";
            row.input.placeholder = v.source === "env"
                ? `Using the ${v.env_var} environment variable (${v.hint})`
                : v.set ? `Saved (${v.hint})` : "Not set";
            row.checkBtn.disabled = !v.set;
            row.clear.disabled = v.source !== "settings";
        }
        this.autoLookup.checked = view?.options?.auto_lookup !== false;
    }

    async request(path, body) {
        const response = await api.fetchApi(path, body === undefined ? {} : {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data?.error || `HTTP ${response.status}`);
        return data;
    }

    async show() {
        this.element.style.display = "flex";
        try {
            this.render(await this.request("/model_linker/settings"));
        } catch (error) {
            notifyError("Could not load settings", error);
        }
    }

    close() {
        this.element.style.display = "none";
    }

    async save(updates, thenCheck = null) {
        try {
            this.render(await this.request("/model_linker/settings", updates));
            if (thenCheck) await this.check(thenCheck);
        } catch (error) {
            notifyError("Could not save settings", error);
        }
    }

    async check(provider) {
        const { status } = this.rows[provider];
        status.textContent = "Checking…";
        try {
            const result = await this.request(`/model_linker/settings/check/${provider}`, {});
            status.textContent = result.ok
                ? `✓ Valid${result.user ? ` — signed in as ${result.user}` : ""}`
                : `✗ ${result.error}`;
        } catch (error) {
            status.textContent = `✗ ${error.message}`;
        }
    }
}
```

`status.textContent` is used rather than `innerHTML`, so a username returned by the provider cannot inject markup.

In `web/modules/linker-dialog.js`: add `import { SettingsDialog } from "./settings-dialog.js";` beside the overrides import, and in `createHeader` insert, directly before the `model-linker-overrides-btn` button, a copy of that button with `id: "model-linker-settings-btn"`, `title: "Tokens and background lookups"`, `textContent: "⚙ Settings"` and `onclick: () => { if (!this.settingsDialog) this.settingsDialog = new SettingsDialog(); this.settingsDialog.show(); }` (same `style` object).

- [ ] **Step 10: Run the whole suite**

Run: `python tests/run.py`
Expected: all pass.

- [ ] **Step 11: Verify in the app**

Restart ComfyUI. Open Model Linker → ⚙ Settings. Save a HuggingFace token: the field clears and shows `Saved (…xxxx)`, then Check reports `✓ Valid — signed in as <you>`. Untick the background-lookup box, close and reopen: it stays unticked. In the browser devtools Network tab, confirm no response from `/model_linker/settings` contains the token. Confirm `data/settings.json` exists and `git status` does not list it.

- [ ] **Step 12: Commit**

```bash
git add core/settings.py core/routes_settings.py web/modules/settings-dialog.js web/modules/linker-dialog.js __init__.py requirements.txt pyproject.toml tests/test_settings.py tests/js/test_settings_dialog.mjs
git commit -m "Add server-side settings for API tokens and background lookups"
```
