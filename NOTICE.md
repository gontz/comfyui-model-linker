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
