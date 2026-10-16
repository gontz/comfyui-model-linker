# Third-party notices

## Upstream

This project is a fork of [kianxyzw/comfyui-model-linker](https://github.com/kianxyzw/comfyui-model-linker),
MIT licensed, © 2025 kianxyzw. Both copyright lines are retained in `LICENSE`.

## Ideas adopted from Comfyui-Model-Resolver

Two design ideas are adopted from
[Azornes/Comfyui-Model-Resolver](https://github.com/Azornes/Comfyui-Model-Resolver)
(MIT, © 2026 Azornes), itself a descendant of this project. The implementations here
are our own; what was borrowed is the approach:

- **Family-name scoring** — comparing model filenames with precision and quantisation
  tokens stripped, so variants of one model score as the same model. See
  `core/matcher.py`.
- **Category alias groups** — modelled on their `CATEGORY_MAP`, since one folder of
  models is reachable under several category names. See `core/categories.py`.

Both are noted in the module docstrings at the point of use.

## Bundled model data

Ported from kianxyzw/comfyui-model-linker (MIT, commit `bec5c80`):

- `metadata/hf-index.json` — every model file in the Comfy-Org and Kijai HuggingFace
  repos, with size and SHA-256 (LFS oid). Rebuild with `python tools/build_hf_index.py`.
- `metadata/model-list.json` — derived from ComfyUI-Manager's model list.
- `metadata/popular-models.json`, `metadata/model-aliases.json` — curated by upstream.
- `core/sources/*`, `core/source_chain.py`, `core/hf_downloader.py`, `core/_hf_worker.py`,
  `core/settings.py` — ported and modified; see each file's history.
