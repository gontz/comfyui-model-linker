# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ComfyUI Model Linker is a ComfyUI extension that helps users relink missing models in workflows. It scans workflow nodes for model references that no longer exist on disk, uses fuzzy matching (via `difflib.SequenceMatcher`) to suggest replacements, and remembers user selections as persistent overrides.

This is **not** a custom node — it provides no `NODE_CLASS_MAPPINGS`. It registers API routes on `PromptServer` and loads a web UI via `WEB_DIRECTORY`.

### This fork does not track upstream

The repo began as a fork of [kianxyzw/comfyui-model-linker](https://github.com/kianxyzw/comfyui-model-linker)
(MIT — see `LICENSE` and `NOTICE.md`), but **`main` is this fork's own line of work**. The
`upstream` remote has been removed deliberately.

- **Do not add an `upstream` remote, and do not merge or rebase from kianxyzw.** The last
  sync brought in a download subsystem whose frontend was written against the monolithic
  `web/linker.js` this project has since split into `web/modules/`; reconciling the two
  cost a full manual merge and lost upstream's UI layer anyway.
- **There is deliberately no download feature.** Upstream's came across in that merge with
  its backend intact and its UI lost, and was then removed on purpose — see below.
- Wanting something from upstream means porting that specific change by hand.

### The removed download subsystem

`core/downloader.py`, `core/sources/*` (HuggingFace, CivitAI, popular, model-list),
`metadata/*.json` and six `/model_linker/` routes (`search`, `download`, `progress`×2,
`cancel`, `directories`) were deleted in commit *"Remove the download subsystem"*. They
worked; they had no interface after the frontend split, and one part of them was not
dormant:

> `/analyze` searched HuggingFace and then CivitAI for **every missing model without a
> 100% local match** — measured at ~0.19s and ~0.30s each on a miss, run serially, so a
> workflow with nine missing models added 4–5s to an analysis that takes ~0.06s warm — and
> sent workflow filenames to both services. There was no setting to disable it.

Recover any of it with `git show <that commit>^:core/downloader.py` and so on.

**Not to be confused with the download link we do offer:** `properties.models[].url` from
the workflow itself is surfaced as `source_url` and rendered by the dialog. That is local,
requires no network call during analysis, and stays.

## Development Environment

- **Python >=3.8**, no external dependencies beyond ComfyUI's own (aiohttp, folder_paths, server)
- **JavaScript**: ES6 modules loaded by ComfyUI from `web/`
- **Activate venv**: `call c:\ComfyUI\.venv\Scripts\activate`
- **No linter, no build step** — the extension is loaded directly by ComfyUI at startup
- **Tests**: `python tests/run.py` — stdlib only, no ComfyUI. The suite builds its own
  model library in a temp directory and answers `import folder_paths` with a fake, so it
  never touches the developer's models or saved overrides. The frontend suites need Node
  and are skipped without it. See `tests/README.md`
- **To see changes in the app**: restart ComfyUI (Python changes) or hard-refresh the
  browser (JS changes)

## Architecture

### Data Flow
```
Frontend (web/modules/linker-dialog.js) → POST /model_linker/analyze → core/linker.py
  → workflow_analyzer.py extracts model refs from all nodes (including subgraphs)
  → scanner.py finds available models via ComfyUI's folder_paths
  → matcher.py fuzzy-matches missing models to available ones
  → overrides.py checks for saved user preferences
  → Returns missing models with ranked suggestions to frontend

Frontend → POST /model_linker/resolve → core/linker.py
  → workflow_updater.py patches the workflow JSON
  → overrides.py persists user selections to data/overrides.json
```

### Key Modules

| Module | Responsibility |
|---|---|
| `__init__.py` | Extension entry point; registers 7 aiohttp API routes on `PromptServer` |
| `core/linker.py` | High-level API: `analyze_and_find_matches()`, `apply_resolution()` |
| `core/scanner.py` | Discovers model files using ComfyUI's `folder_paths` |
| `core/matcher.py` | Banded, model-family-aware matching (rapidfuzz when available) |
| `core/categories.py` | Canonical category names — many aliases reach one model folder |
| `core/workflow_analyzer.py` | Extracts model references from workflow JSON, handles subgraph definitions |
| `core/node_schema.py` | Asks installed node classes which folder each widget loads from |
| `core/node_adapters.py` | Per-node-pack readers for references stored in non-standard shapes |
| `core/workflow_updater.py` | Patches `widgets_values` in workflow nodes, supports subgraph nodes |
| `core/overrides.py` | CRUD for `data/overrides.json` — persistent user model selections |
| `web/linker.js` | Frontend entry point; the dialog and helpers live in `web/modules/` |

### API Routes

All routes are prefixed `/model_linker/`:
- `POST /analyze` — missing models with ranked suggestions, plus `present_models`: the
  inventory of what the workflow *did* find, which the walk already computes
- `POST /resolve` — apply selected resolutions to workflow
- `GET /models` — list all available models
- `GET /overrides` — get saved overrides
- `POST /overrides/delete` — delete single override by key
- `POST /overrides/clear` — clear all overrides
- `POST /overrides/replace` — replace entire overrides document
- `GET /preview` — preview image/video sitting alongside a model
- `POST /reveal` — open the file manager with a model selected (see `core/reveal.py`)

Both `/preview` and `/reveal` take a **category and filename, never a path**, and confirm
the resolved file sits inside a directory registered for that category. `/reveal` runs a
program on the host, so it additionally refuses any request that did not come from this
machine.

## Critical Conventions

### Path Handling (Windows/Unix compatibility)
- **Always use `os.path` methods** — never hardcode separators or normalize to forward slashes
- ComfyUI uses OS-native separators; the extension must match this behavior
- Filename splitting uses regex `[/\\]` to handle both separators in stored paths

### Physical File Identity (symlinks and junctions)
Category directories are frequently links to one shared folder — `models/checkpoints`,
`models/unet`, `models/diffusion_models` and `models/diffusers` all pointing at the same
place is common. Every such alias catalogues the same file again.

- `os.path.normpath()` is **purely lexical and does not resolve links** — never use it to
  detect duplicates
- The scanner records `real_path` per entry (resolved once per directory, since `os.walk`
  visits each root once) and `linker.physical_file_key()` is the identity used everywhere
- Candidates are collapsed to one entry per physical file **before** matching, preferring
  the entry whose category matches the node's, so the written path resolves against the
  right folder

### Scanning
- Only files with known model extensions are catalogued. A category declaring **no**
  extension filter means "unknown", not "accept everything" — otherwise sidecar files
  (`.json`, `.civitai.info`, `.preview.png`, `.lock`) become selectable replacements
- Links are followed, so the walk guards against cycles by tracking resolved directories
- `get_model_files()` caches its result, invalidated by per-directory mtimes plus the
  configured category→paths layout (custom nodes can register paths after startup)

### Fuzzy Matching
Plain edit distance ranks model filenames **backwards**: `qwen3vl_8b_int8` differs from
`qwen3vl_4b_int8` by one character but is a *different model*, while it differs from
`qwen3vl_8b_bf16` by several and is the *same model* at another precision. Scoring is
therefore banded, strongest signal first — see `calculate_filename_confidence`:

| band | score | meaning |
|---|---|---|
| exact | 100 | identical after normalization |
| same family | 94.0–95.9 | same model, different precision/quantization |
| different family | ≤93.9 | capped, so it can never outrank the same model under another name |
| same signature | ~85 | same architecture and size, different wording |
| capacity/generation conflict | ≤69 | `8b` vs `4b`, `qwen3` vs `qwen2` — pinned below the plausibility threshold |

- "Family" = filename with precision/quantization tokens stripped (`normalize_model_family`);
  "signature" = (architecture, parameter count) from that family (`get_model_signature`)
- Raw similarity only orders candidates *within* a band, never across bands
- Filenames normalize to lowercase, extension removed, `_-.` → spaces (dots included:
  `flux1-dev.fp8` and `flux1_dev_fp8` are the same name)
- **rapidfuzz** is used when installed, `SequenceMatcher` otherwise — keep it an optional
  import. `SequenceMatcher.ratio()` is **not symmetric**; the target must stay the first argument
- `find_matches` keeps a `heapq` of `max_results` rather than scoring-then-sorting the library,
  and memoizes normalized names on the candidate dict (`_norm`), which persists via the scan cache
- A saved override is scored 100% **and placed first** by `analyze_and_find_matches`.
  It has to be *placed*, not sorted in: re-sorting the list by confidence would undo the
  category preference below, which `find_matches` established deliberately
- Minimum threshold is 70% confidence
- A match at or above that threshold from the node's own category outranks a better-scoring
  one from another category — a path only resolves against its own category's folder.
  Weaker same-category matches get no boost, so a poor category guess is still recoverable

### Category Aliases (`core/categories.py`)
One folder of models is reachable under several category names: core registers
`diffusion_models` for both `models/unet` and `models/diffusion_models`, custom nodes add
`unet_gguf`/`model_gguf`/`select_safetensors`, and installs commonly link `models/clip` to
`models/text_encoders`. **Never compare category names literally** — use
`canonical_category()` / `categories_match()`. Selection still prefers the exact name when
present, falling back to any alias. The canonical name is sent to the frontend as
`canonical_category` so the picker's scoping agrees with the backend.

### Node Introspection (`core/node_schema.py`)
`NODE_TYPE_TO_CATEGORY_HINTS` is a **fallback**, not the source of truth. It lists ~20 node
types; this install has **526** with model widgets. Categories are instead learned by
standing in for `folder_paths.get_filename_list` while a node declares its inputs, then
seeing which widget caught the sentinel — this works for custom nodes we've never heard of
and resolves ~75% of them.

- Patch both `folder_paths.get_filename_list` **and** the binding in the node's own module
  globals — many packs do `from folder_paths import get_filename_list`
- Only `BOOLEAN/COMBO/FLOAT/INT/STRING` occupy `widgets_values` slots; typed inputs are
  links. An INT with `control_after_generate` occupies **two** slots
- V1 and V3 nodes share one path: V3 exposes `INPUT_TYPES()` over `define_schema()`
- Results are cached per node type; the patch window is lock-guarded and restored in `finally`
- **Limit:** packs that build their choice list without `get_filename_list` (their own
  `os.listdir`) are invisible to this. That is the bulk of the unresolved 25%

### Custom Node Adapters (`core/node_adapters.py`)
One adapter per node pack that stores references in its own shape. Add a pack here rather
than scattering special cases through the analyzer and updater.

- **rgthree Power Lora Loader needs no adapter** — it stores objects directly in widget
  slots (`{"on":…, "lora":"x.safetensors"}`), which `NESTED_MODEL_KEYS` already reads
- **Lora Manager does**: a *list of objects* in one slot, with **extension-less** names
  (`[{"name":"some_lora","strength":"0.80"}]`). Neither looks like a filename to the
  generic scan. Locate the list **by shape, not a fixed index** — the position differs
  between node types and across releases
- Extension-less values resolve via `resolve_model_reference(..., extension_less=True)`,
  which matches the pack's own lookup (basename, extensions stripped, `/` separators)
- Write back in the pack's format: no extension, forward slashes, and keep the companion
  `<lora:name:strength>` text token in sync
- A reference is identified by `(node_id, widget_index, list_index, subgraph_id)`. In the
  frontend **always** build element ids and pending-selection keys via `refSlot()`/`refKey()`
  — several loras share one node and widget index, and ignoring `list_index` collides them

### Workflow Metadata (`properties.models`)
Recent frontends attach `[{name, url, directory, hash?, hash_type?}]` to nodes referencing
models. **`name` is the model filename** (the widget's value), not the input name — look it
up by value. It supplies the authoritative category, and a download URL for models that no
longer exist anywhere on disk.

### Frontend (`web/`)
ES modules, served by ComfyUI from `WEB_DIRECTORY` — subdirectories included, so relative
imports work with no build step. Import paths climb one level further from `web/modules/`:
`../../../scripts/app.js`.

| File | Responsibility |
|---|---|
| `web/linker.js` | Entry point only: `registerExtension`, and opening the dialog |
| `web/modules/linker-dialog.js` | The main dialog: missing models, suggestions, model picker |
| `web/modules/overrides-dialog.js` | The saved-overrides manager |
| `web/modules/util.js` | `escapeHtml`, `safeHttpUrl`, `refSlot`/`refKey`, `isSelectableModel`, `notifyError` |
| `web/modules/dom.js` | `$el` |

- `$el()` is defined **locally**; `scripts/ui.js` is deprecated (announced for removal in
  frontend v1.34) and must not be imported. Only `scripts/app.js` and `scripts/api.js` are
- Registers `commands`, `menuCommands`, `keybindings`, `settings` and `getCanvasMenuItems`.
  Never scrape the DOM for a place to inject UI — the frontend is Vue-rendered
- Use `app.extensionManager.toast` for errors, never `alert()`/`confirm()` (they block the page)
- **Escape everything interpolated into HTML.** Model names, node types and original paths
  all come from the workflow file, which may have been downloaded from anywhere. This holds
  for saved overrides too — an override *records* a workflow-supplied name, so the overrides
  manager renders untrusted text however local the file looks. Use `escapeHtml()`, and pass
  URLs through `safeHttpUrl()`
- Only one analysis may render: a new one aborts the request in flight, and a superseded
  answer is discarded. Without that, two overlapping analyses both complete and whichever
  *arrives* last wins
- CSS is scoped with specific IDs/classes prefixed `model-linker-` to avoid ComfyUI style conflicts
- Modal position/size persists via `localStorage`

### Overrides Persistence
- Stored at `data/overrides.json` (gitignored)
- Format: `{version: 1, mappings: [{key: "category:normalized_name", path: "...", ...}]}`
- Keys are `category:normalized_filename` for category-specific lookup
