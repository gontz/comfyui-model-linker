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
| `test_matcher.py` | banded, family-aware scoring; category preference |
| `test_scanner.py` | what counts as a model, linked directories, the scan cache |
| `test_linker.py` | physical-file identity, candidate selection, the picker's catalogue |
| `test_analyzer.py` | reading references out of a workflow, including subgraphs |
| `test_adapters.py` | node packs storing references in their own shape |
| `test_updater.py` | patching paths back in, including into subgraph definitions |
| `test_overrides.py` | persisted user selections |
| `test_reveal.py` | opening the file manager: what it refuses, and how it launches |
| `js/` | `web/`: registration, escaping, picker filtering, reference identity, overlapping analyses |

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
