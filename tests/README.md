# Tests

```
python tests/run.py            # everything
python tests/run.py -v         # naming each test
python tests/run.py --python   # skip the frontend suites
```

No dependencies beyond the standard library, and no ComfyUI: the suite builds
its own model library in a temporary directory and answers `import
folder_paths` with `fake_folder_paths.py`. Nothing reads or writes the
developer's real models or saved overrides.

The frontend suites need Node on `PATH`. Without it they are reported as
skipped rather than failing, since the Python half stands on its own.

Individual modules run under plain unittest too:

```
python -m unittest discover -t . -s tests -p "test_matcher.py" -v
```

## Layout

| File | Covers |
|---|---|
| `test_categories.py` | alias groups — one folder reachable under several category names |
| `test_matcher.py` | banded, family-aware scoring; category preference; word-order similarity for reordered filename tokens |
| `test_scanner.py` | what counts as a model, linked directories, the scan cache; `NON_MODEL_CATEGORIES` now also covers `datasets` (alongside `custom_nodes`/`configs`); concurrent cold calls to `get_model_files()` trigger only one scan |
| `test_linker.py` | physical-file identity, candidate selection, the picker's catalogue |
| `test_analyzer.py` | reading references out of a workflow, including subgraphs; the basename fallback's "claimed" guard — a shared-basename entry another widget on the same node names exactly is never reused for a different widget |
| `test_adapters.py` | node packs storing references in their own shape |
| `test_updater.py` | patching paths back in, including into subgraph definitions |
| `test_overrides.py` | persisted user selections; `resolutions_to_record()` drops `record_override: false` entries (Swap) |
| `test_reveal.py` | opening the file manager: what it refuses, and how it launches |
| `test_inventory.py` | the found-on-disk list that accompanies the missing one; `source_url`/`source_hash`/`source_hash_type` are kept on a present model's summary so Export can reuse the workflow's own link |
| `test_node_schema.py` | per-node-type category introspection via the `get_filename_list` sentinel: retried for a node type that registers late, and thread-safe (other threads see the real list, not the sentinel, during a concurrent introspection) |
| `test_node.py` | the `ModelLinker` node: no inputs/outputs, never executes |
| `test_settings.py` | the settings store: tokens never echoed in errors, `auto_lookup` and other options, control-character token rejection |
| `test_sources.py` | the ported download sources (`civitai`, `civarchive`, `huggingface`, `hf_index`, `popular`, `model_list`); host checks are exact, not substring |
| `test_source_chain.py` | `find_download_source`/`find_by_hash`; never contacts a workflow's own URL |
| `test_lookups.py` | the background lookup store/worker; `lookup_key()` scoping (`url:<16hex>:<key>` when a workflow URL is present) |
| `test_downloader.py` | the download queue: physical-identity duplicate guard, `NON_MODEL_CATEGORIES` refused as destinations, no-clobber renames, https enforced on every redirect hop, hash verified before naming |
| `test_hf_routing.py` | the HuggingFace multi-connection engine: pinned `https://huggingface.co` endpoint, path containment, never marks `completed` before hashing |
| `test_lm_sidecar.py` | Lora Manager sidecar/preview writing: no-clobber landing, case-insensitive folder discovery, `autov3` omitted (not `null`) when CivitAI has none; validates against Lora Manager's published schema when it is installed alongside |
| `test_online_search.py` | scores and returns similar-model candidates (CivitAI + HuggingFace); one failing source doesn't hide the others, tokens go only to their own host, a total outage is reported rather than returning empty — the same-basename refusal itself is frontend-only, see `js/test_downloads.mjs` |
| `test_hf_identify.py` | identifying a HuggingFace repo by SHA-256 |
| `test_lm_backfill.py` | the Lora Manager HuggingFace backfill: no forced scans, `linked_elsewhere` for non-HF links, `hydrated` verified against pre-hydration values (else `no_card`), AI gated to models with no trigger words/notes/usage tips/base model, `trainedWords_before` restore trail, `interrupted` after a restart, `limit` |
| `test_exporter.py` | `strip_secrets` (userinfo, fragment, `;params`, secret query keys incl. AWS/GCS, losslessly); the exported note and node links |
| `js/test_extension.mjs` | registration (`commands`, `settings`, `afterConfigureGraph`, `nodeCreated`, `getCanvasMenuItems`); only `scripts/app.js`/`scripts/api.js` imported |
| `js/test_escaping.mjs` | escaping workflow-supplied strings into HTML; URLs through `safeHttpUrl()` |
| `js/test_reference_keys.mjs` | `refSlot()`/`refKey()` reference identity |
| `js/test_picker.mjs` | model picker filtering |
| `js/test_analyze_race.mjs` | overlapping analyses: the latest request wins, a superseded answer is discarded |
| `js/test_perfect_match.mjs` | `isPerfectMatch()`, used instead of comparing confidence to 100 directly |
| `js/test_settings_dialog.mjs` | the ⚙ Settings dialog: tokens, `auto_lookup`, Lora Manager HuggingFace fill controls |
| `js/test_downloads.mjs` | `DownloadController`: keyed by canonical file key, reconnect after reload, 🔎 Search similar (refuses a candidate whose basename matches any on-screen card's, by basename alone, never scoped to category), cancel |
| `js/test_locate.mjs` | Locate, including into a nested subgraph; the empty-result notification when a subgraph has no live instance |
| `js/test_swap.mjs` | Swap: one queued resolution per node sharing a model, `record_override: false`, queue-panel removal by `swap_group` key |
| `js/test_export.mjs` | Export: `strip_secrets` round-trip, the "Model links" note, the muted Model Linker node, re-applying without duplicating |

Suites that need `requests` (`test_sources.py`, `test_source_chain.py`, `test_downloader.py`,
`test_hf_routing.py`, `test_online_search.py`, `test_hf_identify.py`, `test_lm_backfill.py`,
`test_lm_sidecar.py`) are reported as skipped, not failing, when it is not installed.

`support.py` holds the fixtures. `LibraryTestCase` gives each test a temporary
`models/` tree wired into the fake `folder_paths`; `add_models` creates files
and registers a category, `link_category` makes a second category a real
filesystem link to another's directory, and `alias_category` points two
categories at one directory without a link.

## Notes for adding tests

- **Links are the point, not an edge case.** Category directories are usually
  links to one shared folder, so most identity bugs only appear when two
  categories reach the same physical file. `link_category` skips the test
  where the platform forbids links (Windows without Developer Mode), so an
  invariant that must hold everywhere needs `alias_category` as well.
- **Say what the invariant is, not what the code does.** These tests exist to
  survive a rewrite of the thing they cover.
- **The frontend harness copies the live `web/` tree every run.** An earlier
  version imported a stale copy and went on reporting a setting as registered
  after it had been deleted. Do not reintroduce a checked-in copy. Use
  `loadModule('modules/util.js')` to reach exported helpers, `loadExtension()`
  for what gets registered, and `readAllSources()` for bans that must hold
  across every module rather than just the entry point.
- Assertions about source text (no `alert()`, no scraping ComfyUI's markup)
  must be anchored so they match code rather than the comments explaining it.
