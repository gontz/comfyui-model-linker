# Plan B — Downloads, Background Lookups and Lora Manager Sidecars Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find downloads for missing models in the background (HuggingFace, CivitAI, CivArchive, curated lists, hash lookups), download them into the right model folder from a server-side queue, and write a Lora Manager–compatible `<stem>.metadata.json` plus preview next to every downloaded model.

**Architecture:** Upstream's source modules are ported file-by-file into `core/sources/` and `core/source_chain.py`, minus the HEAD request to workflow URLs. A new `core/lookups.py` keeps a persistent store (`data/lookups.json`) and one background worker thread; `/analyze` attaches cached answers and queues the rest, and the frontend polls `/model_linker/lookups` and patches cards in place. A new `core/downloader.py` runs a two-worker queue that streams to `.part`, hashes while streaming, validates the destination, and calls completion hooks; HuggingFace URLs go through upstream's `huggingface_hub` engine (`core/hf_downloader.py` + `core/_hf_worker.py`). A completion hook in `core/lm_sidecar.py` looks the hash up on CivitAI (`core/civitai_meta.py`), writes the sidecar and preview, and pokes Lora Manager to index the file. The frontend gets one new module, `web/modules/downloads.js`.

**Tech Stack:** Python ≥3.9, `requests` (declared in Plan A), optional `huggingface_hub` ≥1.1, aiohttp routes, ES modules, `python tests/run.py`.

**Spec:** `docs/superpowers/specs/2026-10-14-upstream-parity-design.md`

**Requires Plan A** — specifically `core/settings.py` (`get_token`, `provider_for_url`, `auth_headers_for_url`, `get_option('auto_lookup')`), `isPerfectMatch` in `web/modules/util.js`, the `category_mismatch` match field, `asyncio` in `__init__.py`, and the dialog's ⚙ Settings button.

## Revisions (2026-10-16)

Review against the current code, bec5c80 and Lora Manager v1.2.5. Every code block below was re-run in a scratch copy (Plan A settings + this plan) before these edits were accepted.

- Task 1: civarchive no longer caches `None` when the request itself failed (new Step 6, test `test_a_failed_request_is_not_cached`).
- Task 3: `lookups._find` drops the sources' in-process caches for the request first, so "Search again" and the 24 h expiry really ask again (test in `test_source_chain.py`).
- Task 3: download-route registration catches every exception (logged), so a bug there can no longer take `/analyze` down with it.
- Task 3: `lookup_key` deliberately keeps the lowercase *basename*, not the spec's normalized name: normalization drops the extension, which would make `x.gguf` and `x.safetensors` share one download.
- Task 4: `resolve_destination` rejects `:` in the raw name — on Windows `basename('C:evil.safetensors')` is `evil.safetensors`, so the old check never fired.
- Task 4: `_stream` refuses to rename over a file that appeared mid-download (test `test_file_appearing_mid_download_is_not_overwritten`); `DownloaderTestCase` redirects `overrides._data_dir`.
- Task 4: `_hf_engine_for` imports through `importlib`, so the "library missing" fallback test no longer depends on test order.
- Task 5: the port is no longer "no edits": the engine must update the shared record in place (it replaced it, dropping `path`, which blinded the duplicate-download guard) and must not rename over an existing file. New `RecordTests`.
- Task 6: Lora Manager's `settings.json` is located the way Lora Manager does (`LORA_MANAGER_SETTINGS_DIR` → portable flag → per-user config folder); `write_sidecar` gains `fetch_if_unknown`; the scan after a download is scoped to the model's root; the never-matching `hunyuan video` mapping is dropped.
- Task 7/8: `data-ml-slot` and block ids are escaped; block lookup uses `CSS.escape`.
- Task 9: `lm_api.base_models()` reads `{"success", "data": {"models": [...]}}` (the old code returned `['success', 'data']`); `hydrate_huggingface` turns a cross-drive `relpath` `ValueError` into `LoraManagerUnavailable`; `scan` takes an optional root.
- Task 9: `hf_identify.clear_cache()` runs at the start of every backfill; `candidates()` skips `exclude` / `skip_metadata_refresh` models; AI enrichment failure is recorded as `ai_error` and no longer fails the run.
- Task 9: `lm_backfill.start(use_ai, limit)` and the start route take a `limit`; Step 11 now runs a 5-model sample first and stops for the user, as the hand-off requires.
- Task 9 Step 7 is now code: HuggingFace downloads are written with `fetch_if_unknown=False`, then linked and hydrated.

## Global Constraints

- Port upstream with `git show bec5c80:<path> > <path>`; fallback for a clone without that object: `gh api "repos/kianxyzw/comfyui-model-linker/contents/<path>?ref=bec5c80" -H "Accept: application/vnd.github.raw" > <path>`. Never add a remote, merge or rebase.
- Lora Manager is GPL-3.0; this extension is MIT. Implement the sidecar **format** from its documentation (spec, "Lora Manager sidecar contract"). Copy no Lora Manager code.
- Nothing online happens inside `/analyze`'s own request: it only reads the lookup store and queues work.
- No outbound request to a URL taken from a workflow except the download the user starts. (Upstream HEADs it during analysis; that is removed.)
- Tokens go only to `settings.provider_for_url` hosts, only as an `Authorization: Bearer` header, never in a URL.
- Downloads: `https` only; filename = basename; no `:` in it; extension in `scanner.MODEL_EXTENSIONS`; destination passes `reveal._is_within(dest, category_dir)`; never overwrite an existing file; free space ≥ size + 512 MiB.
- `file_path`/`preview_url` in sidecars: `os.path.abspath(path).replace('\\', '/')` built from the ComfyUI-registered directory — **never `os.path.realpath`** (this repo uses realpath for identity everywhere else; here it would produce a path Lora Manager does not key on).
- Every new Python test module that needs `requests` starts with `requests = __import__('requests')` inside a `try`, raising `unittest.SkipTest` if missing, so the suite still runs without it.
- All frontend HTML built from workflow or network data goes through `escapeHtml()`; links through `safeHttpUrl()` and must be `https:` to be downloadable.
- `python tests/run.py` is green at the end of every task.
- **Interpreter:** every `python` here means `C:/ComfyUI/.venv/Scripts/python.exe` (has `requests` 2.34 and `huggingface_hub` 1.33). Under another interpreter this plan's suites skip themselves and "pass" vacuously — the unittest summary must not report `skipped=` for `test_sources`, `test_source_chain`, `test_downloader`, `test_hf_routing` or `test_lm_sidecar`'s schema test.

## Review Focus

- A download whose server closes the connection early (content-length 4 GB, 1 GB received) must not leave a model-named file behind. Pinned in Task 4 (`test_short_body_leaves_nothing`).
- A workflow listing the same model on five nodes must start **one** download, and all five cards must show its progress. Pinned in Task 7 (`downloadAll` dedupe check) and server-side by the "already downloading" refusal in Task 4.
- Closing the dialog mid-download, then reopening it, must show the running download's progress on the right card. Pinned in Task 7 (reconnect check).
- CivitAI being unreachable while a download finishes must still produce a sidecar Lora Manager can load (all required keys, `from_civitai: false`). Pinned in Task 6.
- A lookup result arriving while the user has queued selections must not discard them — results patch cards in place. Pinned in Task 7 (`pollLookups` never re-renders).

---

### Task 0: Record the reversal in CLAUDE.md before porting anything

**Files:**
- Modify: `CLAUDE.md` ("This fork does not track upstream" bullets; "The removed download subsystem" section)

**Interfaces:** none. This exists because CLAUDE.md currently says, in bold, "There is deliberately no download feature" — and every agent executing the tasks below reads CLAUDE.md first and treats it as overriding.

- [ ] **Step 1: Edit CLAUDE.md**

1. Replace the bullet starting `- **There is deliberately no download feature.**` with:

```markdown
- **Downloads were reinstated on 2026-10-14 by the user's decision**, redesigned so the
  objections below no longer hold — see `docs/superpowers/specs/2026-10-14-upstream-parity-design.md`
  and the plans beside it. Upstream code for it is ported file-by-file with
  `git show bec5c80:<path>`; that is "porting by hand", not a merge.
```

2. Rename the heading `### The removed download subsystem` to `### Why downloads were once removed (history)` and append one paragraph:

```markdown
The reinstated design answers both points: `/analyze` only reads a persistent lookup
store and queues work for a background thread, so it stays as fast as before; lookups
can be switched off (`auto_lookup` in ⚙ Settings); and nothing contacts a workflow's own
URL during lookup. Plan C Task 5 completes this documentation.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "Record that downloads are being reinstated, before porting them"
```

---

### Task 1: Port the download sources

**Files:**
- Create (ported): `core/sources/__init__.py`, `core/sources/civitai.py`, `core/sources/civarchive.py`, `core/sources/huggingface.py`, `core/sources/hf_index.py`, `core/sources/popular.py`, `core/sources/model_list.py`, `metadata/hf-index.json`, `metadata/model-list.json`, `metadata/popular-models.json`, `metadata/model-aliases.json`, `tools/build_hf_index.py`
- Modify: `core/matcher.py` (add `is_variant_token`), `core/sources/huggingface.py` (host check), `NOTICE.md`
- Test: `tests/test_sources.py` (new)

**Interfaces:**
- Consumes: `settings.provider_for_url(url) -> Optional[str]` (Plan A).
- Produces:
  - `matcher.is_variant_token(token: str) -> bool`
  - `sources.huggingface.parse_huggingface_url(url) -> Optional[{'repo', 'branch'?, 'filename'}]` — now only for real HuggingFace hosts (and `hf://`).
  - `sources.huggingface.search_huggingface_for_file(filename, token=None, exact_only=False) -> Optional[dict]`
  - `sources.civitai.search_civitai_for_file(filename, api_key=None, exact_only=False)`, `search_civitai_by_hash(hash, api_key=None)`
  - `sources.civarchive.lookup_by_sha256(sha256, filename='') -> Optional[dict]`
  - `sources.hf_index.lookup_by_filename(filename)`, `lookup_by_sha256(sha256)` → `{source, url_source:'hf_index', repo_id, filename, path, url, size, sha256, match_type}`
  - `sources.popular.get_popular_model_url(filename)`, `sources.model_list.search_model_list(filename, exact_only=False)`

- [ ] **Step 1: Copy the files**

```bash
mkdir -p core/sources metadata tools
for f in core/sources/__init__.py core/sources/civitai.py core/sources/civarchive.py \
         core/sources/huggingface.py core/sources/hf_index.py core/sources/popular.py \
         core/sources/model_list.py metadata/hf-index.json metadata/model-list.json \
         metadata/popular-models.json metadata/model-aliases.json tools/build_hf_index.py; do
  git show "bec5c80:$f" > "$f"
done
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_sources.py`:

```python
"""
Download sources. Network calls are replaced with fakes; the bundled
HuggingFace index is read as shipped.
"""

import unittest

try:
    import requests  # noqa: F401
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core.matcher import is_variant_token
from core.sources import civarchive, hf_index
from core.sources.huggingface import build_search_queries, parse_huggingface_url

SHA = 'df7c757437ef3696e76ee5cc18c063681478639132547b6292b0b4d773814ba5'


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class VariantTokenTests(unittest.TestCase):
    def test_precision_and_quant_tokens(self):
        for token in ('fp8', 'bf16', 'fp16', 'q4', 'e4m3fn', 'int8', 'Q8'):
            self.assertTrue(is_variant_token(token), token)
        for token in ('flux1', 'dev', '8b', 'qwen3vl'):
            self.assertFalse(is_variant_token(token), token)


class HuggingFaceTests(unittest.TestCase):
    def test_search_queries_strip_precision_suffix(self):
        self.assertEqual(build_search_queries('flux1-dev-fp8_e4m3fn.safetensors'), ['flux1-dev-fp8_e4m3fn', 'flux1-dev'])
        self.assertEqual(build_search_queries('clip_l.safetensors'), ['clip_l'])

    def test_parses_real_hosts_only(self):
        parsed = parse_huggingface_url('https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/sub%20dir/a.safetensors')
        self.assertEqual(parsed, {'repo': 'Comfy-Org/flux1-dev', 'branch': 'main', 'filename': 'sub dir/a.safetensors'})
        for url in ('https://huggingface.co.evil.example/a/b/resolve/main/c.safetensors',
                    'https://evil.example/huggingface.co/a/b/resolve/main/c.safetensors',
                    'http://huggingface.co/a/b/resolve/main/c.safetensors'):
            self.assertIsNone(parse_huggingface_url(url), url)


class IndexTests(unittest.TestCase):
    def test_finds_comfy_org_file_before_kijai(self):
        result = hf_index.lookup_by_filename('flux1-dev-fp8.safetensors')
        self.assertEqual(result['repo_id'], 'Comfy-Org/flux1-dev')
        self.assertTrue(result['url'].startswith('https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/'))
        self.assertEqual(len(result['sha256']), 64)

    def test_hash_lookup_round_trips(self):
        by_name = hf_index.lookup_by_filename('flux1-dev-fp8.safetensors')
        by_hash = hf_index.lookup_by_sha256(by_name['sha256'].upper())
        self.assertEqual(by_hash['path'], by_name['path'])
        self.assertEqual(by_hash['match_type'], 'hash')

    def test_folder_prefix_is_ignored(self):
        self.assertIsNotNone(hf_index.lookup_by_filename('FLUX\\flux1-dev-fp8.safetensors'))


class CivArchiveTests(unittest.TestCase):
    def setUp(self):
        self._get = civarchive.requests.get
        civarchive._cache.clear()

    def tearDown(self):
        civarchive.requests.get = self._get
        civarchive._cache.clear()

    def test_prefers_a_live_huggingface_mirror(self):
        payload = {'model': {'name': 'Styles', 'type': 'LORA'}, 'files': [
            {'filename': 'a.safetensors', 'url': 'https://civitai.com/api/download/models/1', 'source': 'civitai'},
            {'filename': 'a.safetensors', 'url': 'https://huggingface.co/x/y/resolve/main/gone.safetensors',
             'source': 'huggingface', 'deletedAt': '2025-01-01'},
            {'filename': 'a.safetensors', 'url': 'https://huggingface.co/x/y/resolve/main/gated.safetensors',
             'source': 'huggingface', 'is_gated': True},
            {'filename': 'a.safetensors', 'url': 'https://huggingface.co/x/y/resolve/main/a.safetensors',
             'source': 'huggingface'},
        ]}
        civarchive.requests.get = lambda url, **kw: FakeResponse(200, payload)
        result = civarchive.lookup_by_sha256(SHA, 'a.safetensors')
        self.assertEqual(result['url'], 'https://huggingface.co/x/y/resolve/main/a.safetensors')
        self.assertEqual(result['match_type'], 'hash')

    def test_bad_hashes_make_no_request(self):
        calls = []
        civarchive.requests.get = lambda url, **kw: calls.append(url)
        self.assertIsNone(civarchive.lookup_by_sha256('not-a-hash'))
        self.assertIsNone(civarchive.lookup_by_sha256('0' * 64))
        self.assertEqual(calls, [])

    def test_a_failed_request_is_not_cached(self):
        # An outage is not an answer: the next lookup must ask again
        def down(url, **kw):
            raise civarchive.requests.ConnectionError('down')
        civarchive.requests.get = down
        self.assertIsNone(civarchive.lookup_by_sha256(SHA))
        self.assertNotIn(SHA, civarchive._cache)
        civarchive.requests.get = lambda url, **kw: FakeResponse(503, {})
        self.assertIsNone(civarchive.lookup_by_sha256(SHA))
        self.assertNotIn(SHA, civarchive._cache)
```

- [ ] **Step 3: Run to verify they fail**

Run: `python -m unittest discover -t . -s tests -p "test_sources.py" -v`
Expected: ERROR — `cannot import name 'is_variant_token' from 'core.matcher'`.

- [ ] **Step 4: Add `is_variant_token`**

In `core/matcher.py`, directly after the `_VARIANT_HEAD_RE` definition (line 127), add:

```python
def is_variant_token(token: str) -> bool:
    """True for a precision/quantisation token (fp8, bf16, q4, e4m3fn, int8, ...)."""
    token = (token or '').lower()
    return token in _VARIANT_TOKENS or bool(_VARIANT_TOKEN_RE.match(token))
```

- [ ] **Step 5: Make HuggingFace URL parsing check the real host**

In `core/sources/huggingface.py`, in `parse_huggingface_url`, replace

```python
    parsed = urlparse(url)
    if 'huggingface.co' not in parsed.netloc:
        return None
```

with

```python
    # Decided by the actual host: a workflow-supplied URL on a lookalike host
    # must not be treated (and authenticated) as HuggingFace.
    from ..settings import provider_for_url
    if provider_for_url(url) != 'huggingface':
        return None
    parsed = urlparse(url)
```

- [ ] **Step 6: Do not cache a CivArchive request that failed**

Upstream caches `None` even when `_get` returned nothing (connection error, exhausted 429) or the server errored, so one outage hides a model until ComfyUI restarts. In `core/sources/civarchive.py`, in `lookup_by_sha256`, replace

```python
    result = None
    response = _get(API_URL.format(sha256=sha256))
    if response is not None and response.status_code == 200:
```

with

```python
    result = None
    response = _get(API_URL.format(sha256=sha256))
    if response is None or response.status_code not in (200, 404):
        return None  # no answer, only a failure - ask again next time
    if response.status_code == 200:
```

- [ ] **Step 7: Run the tests**

Run: `python -m unittest discover -t . -s tests -p "test_sources.py" -v`
Expected: PASS. If `test_precision_and_quant_tokens` fails on one token, check whether that token is in `_VARIANT_TOKENS` or matched by `_VARIANT_TOKEN_RE` (`core/matcher.py:110-122`) — adjust the **test** only if the matcher intentionally excludes it, and say so in the commit message.

- [ ] **Step 8: Record the provenance of the data files**

Append to `NOTICE.md`:

```markdown
## Bundled model data

Ported from kianxyzw/comfyui-model-linker (MIT, commit `bec5c80`):

- `metadata/hf-index.json` — every model file in the Comfy-Org and Kijai HuggingFace
  repos, with size and SHA-256 (LFS oid). Rebuild with `python tools/build_hf_index.py`.
- `metadata/model-list.json` — derived from ComfyUI-Manager's model list.
- `metadata/popular-models.json`, `metadata/model-aliases.json` — curated by upstream.
- `core/sources/*`, `core/source_chain.py`, `core/hf_downloader.py`, `core/_hf_worker.py`,
  `core/settings.py` — ported and modified; see each file's history.
```

- [ ] **Step 9: Run the whole suite and commit**

Run: `python tests/run.py` — expected: all pass.

```bash
git add core/sources core/matcher.py metadata tools NOTICE.md tests/test_sources.py
git commit -m "Port the download sources from upstream, checking HuggingFace hosts properly"
```

---

### Task 2: Port the source chain without contacting workflow URLs

**Files:**
- Create (ported): `core/source_chain.py`
- Test: `tests/test_source_chain.py` (new)

**Interfaces:**
- Consumes: Task 1's sources; `settings.get_token`, `settings.provider_for_url`.
- Produces:
  - `source_chain.find_download_source(filename: str, category: str = '', workflow_url: str = '', workflow_directory: str = '', sha256: str = '') -> Optional[dict]` — dict keys: `source, url, filename, directory, match_type` and, depending on step, `url_source, name, size, sha256, page_url, type`.
  - `source_chain.find_by_hash(sha256: str, filename: str = '') -> Optional[dict]`

- [ ] **Step 1: Copy**

```bash
git show bec5c80:core/source_chain.py > core/source_chain.py
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_source_chain.py`:

```python
"""The order sources are asked in, and what is never contacted."""

import unittest
from unittest import mock

try:
    import requests
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import source_chain

SHA = 'df7c757437ef3696e76ee5cc18c063681478639132547b6292b0b4d773814ba5'


def fail(*args, **kwargs):
    raise AssertionError('this source should not have been asked')


class ChainTests(unittest.TestCase):
    def test_workflow_url_is_returned_without_contacting_it(self):
        # Recorded rather than raised: upstream wraps its HEAD in a bare try
        contacted = []
        record = lambda *a, **k: contacted.append(a)
        with mock.patch.object(requests, 'head', record), mock.patch.object(requests, 'get', record):
            result = source_chain.find_download_source(
                'x.safetensors', category='loras',
                workflow_url='https://huggingface.co/a/b/resolve/main/x.safetensors')
        self.assertEqual(contacted, [])
        self.assertEqual(result['url_source'], 'workflow')
        self.assertEqual(result['source'], 'huggingface')
        self.assertIsNone(result['size'])

    def test_lookalike_host_is_not_called_huggingface(self):
        result = source_chain.find_download_source(
            'x.safetensors', workflow_url='https://huggingface.co.evil.example/a/b/resolve/main/x.safetensors')
        self.assertEqual(result['source'], 'workflow')

    def test_index_answers_before_any_network_search(self):
        with mock.patch.object(source_chain, 'search_huggingface_for_file', fail), \
             mock.patch.object(source_chain, 'search_civitai_for_file', fail):
            result = source_chain.find_download_source('flux1-krea-dev_fp8_scaled.safetensors',
                                                       category='diffusion_models')
        self.assertEqual(result['url_source'], 'hf_index')
        self.assertEqual(result['directory'], 'diffusion_models')

    def test_hash_is_tried_before_name_search(self):
        hit = {'download_url': 'https://civitai.com/api/download/models/42', 'filename': 'private.safetensors',
               'url': 'https://civitai.com/models/7', 'name': 'Private'}
        with mock.patch.object(source_chain, 'search_civitai_by_hash', lambda sha, api_key=None: hit), \
             mock.patch.object(source_chain, 'search_huggingface_for_file', fail), \
             mock.patch.object(source_chain, 'search_civitai_for_file', fail):
            result = source_chain.find_download_source('private_renamed.safetensors', category='loras', sha256=SHA)
        self.assertEqual(result['url'], 'https://civitai.com/api/download/models/42')
        self.assertEqual(result['match_type'], 'hash')
```

- [ ] **Step 3: Run to verify they fail**

Run: `python -m unittest discover -t . -s tests -p "test_source_chain.py" -v`
Expected: `test_workflow_url_is_returned_without_contacting_it` FAILS on `contacted == []` (upstream HEADs the URL by default), and `test_lookalike_host_is_not_called_huggingface` FAILS (upstream's `'huggingface.co' in url` substring test). The other two pass already and must keep passing.

- [ ] **Step 4: Edit the ported chain**

In `core/source_chain.py`:

1. Delete `import requests` and change `from .settings import get_token` to `from .settings import get_token, provider_for_url`.
2. Replace `source_from_url` with:

```python
def source_from_url(url: str) -> str:
    """Which service a URL belongs to, by its real host."""
    return provider_for_url(url) or 'workflow'
```

3. In `find_download_source`, remove the `fetch_size: bool = True` parameter and its docstring mention, and replace the whole `# 0. URL from the workflow` block with:

```python
    # 0. URL from the workflow (highest priority - the author's own link).
    # Returned as-is: contacting it here would send a request to whatever host
    # a shared workflow names, merely because the workflow was opened.
    if workflow_url:
        return {
            'source': source_from_url(workflow_url),
            'url': workflow_url,
            'filename': filename,
            'directory': default_directory,
            'match_type': 'exact',
            'url_source': 'workflow',
            'size': None,
        }
```

- [ ] **Step 5: Run the whole suite and commit**

Run: `python tests/run.py` — expected: all pass.

```bash
git add core/source_chain.py tests/test_source_chain.py
git commit -m "Port the source chain; never contact a workflow's URL during lookup"
```

---

### Task 3: Background lookups with a persistent store

**Files:**
- Create: `core/lookups.py`, `core/routes_downloads.py`
- Modify: `__init__.py` (guarded registration; `/analyze` attaches lookups)
- Test: `tests/test_lookups.py` (new), `tests/test_source_chain.py` (append; it needs `requests`)

**Interfaces:**
- Consumes: `source_chain.find_download_source(...)`, `settings.get_option('auto_lookup')`, `overrides._data_dir()`, `categories.canonical_category`.
- Produces:
  - `lookups.lookup_key(missing: dict) -> str` — `'sha256:<hex>'` or `'<canonical category>:<lowercase basename>'`. (The spec says "normalized name"; the basename is kept on purpose — normalizing drops the extension, so `x.gguf` and `x.safetensors` would share one answer, and a lookup is an exact-filename search.)
  - The background lookup (`lookups._find`) first drops this request's entries from the sources' in-process caches (`huggingface._search_cache`, `civitai._search_cache`, `civarchive._cache`): those keep misses until restart, which would turn "Search again" and the 24-hour expiry into a replay of the old miss. The persistent store is the only cache lookups rely on.
  - `lookups.attach_cached(missing_models: list) -> int` — sets on each missing model `lookup_key`, `lookup_state` (`'found' | 'not_found' | 'pending' | 'idle' | 'not_needed'`) and, when known, `download_source` (dict or None); returns how many are pending.
  - `lookups.results_for(keys: list[str]) -> {key: {'state': 'found'|'not_found'|'pending'|'unknown', 'source': dict|None}}`
  - `lookups.refresh(missing: dict) -> str` — re-queues regardless of store and of `auto_lookup` (user-initiated); returns the key.
  - Routes: `POST /model_linker/lookups` `{keys: [...]}` → `{results: {...}}`; `POST /model_linker/lookups/refresh` `{missing: {original_path, category, source_url?, source_hash?, source_hash_type?}}` → `{key, state: 'pending'}`.
  - `/analyze` responses gain `lookups_pending: int` and the per-model fields above.
  - `core/routes_downloads.register(routes, web)` — Task 4 adds download routes to it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lookups.py`:

```python
"""Background lookups: keyed, persisted, never blocking, never repeated needlessly."""

import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from core import lookups, overrides, settings

SHA = 'ab' * 32


def missing(name='flux1-dev-fp8.safetensors', category='checkpoints', **extra):
    return {'original_path': name, 'category': category, 'matches': [], **extra}


class LookupTestCase(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix='model-linker-lookups-')
        self._real = overrides._data_dir
        overrides._data_dir = lambda: self.data_dir
        self.found = {}
        self.calls = []
        self.release = threading.Event()
        self.release.set()

        def find(request):
            self.release.wait(5)
            self.calls.append(request)
            if request.get('raise'):
                raise RuntimeError('network down')
            return self.found.get(request['filename'])

        lookups._store = lookups.LookupStore()
        lookups._worker = lookups.LookupWorker(lookups._store, find)

    def tearDown(self):
        lookups._worker.wait_idle(5)
        overrides._data_dir = self._real
        shutil.rmtree(self.data_dir, ignore_errors=True)


class KeyTests(unittest.TestCase):
    def test_sha256_is_preferred(self):
        self.assertEqual(lookups.lookup_key(missing(source_hash=SHA.upper(), source_hash_type='SHA256')),
                         'sha256:' + SHA)

    def test_other_hash_types_are_ignored(self):
        key = lookups.lookup_key(missing(source_hash='1234abcd', source_hash_type='AutoV2'))
        self.assertEqual(key, 'checkpoints:flux1-dev-fp8.safetensors')

    def test_aliases_share_a_key_and_folders_are_dropped(self):
        a = lookups.lookup_key(missing('sub\\T5XXL.safetensors', 'clip'))
        b = lookups.lookup_key(missing('t5xxl.safetensors', 'text_encoders'))
        self.assertEqual(a, b)


class AttachTests(LookupTestCase):
    def test_unknown_models_are_queued_and_answered(self):
        self.found['flux1-dev-fp8.safetensors'] = {'url': 'https://huggingface.co/a/b/resolve/main/x', 'source': 'huggingface'}
        models = [missing()]
        self.assertEqual(lookups.attach_cached(models), 1)
        self.assertEqual(models[0]['lookup_state'], 'pending')
        lookups._worker.wait_idle(5)
        result = lookups.results_for([models[0]['lookup_key']])[models[0]['lookup_key']]
        self.assertEqual(result['state'], 'found')
        self.assertEqual(result['source']['source'], 'huggingface')

    def test_cached_answer_is_attached_without_asking_again(self):
        lookups.attach_cached([missing()])
        lookups._worker.wait_idle(5)
        models = [missing()]
        self.assertEqual(lookups.attach_cached(models), 0)
        self.assertEqual(models[0]['lookup_state'], 'not_found')
        self.assertEqual(len(self.calls), 1)

    def test_answers_survive_a_restart(self):
        lookups.attach_cached([missing()])
        lookups._worker.wait_idle(5)
        lookups._store = lookups.LookupStore()  # a fresh process reading the same file
        models = [missing()]
        lookups.attach_cached(models)
        self.assertEqual(models[0]['lookup_state'], 'not_found')

    def test_negative_answers_expire(self):
        lookups.attach_cached([missing()])
        lookups._worker.wait_idle(5)
        later = lookups.time.time() + lookups.NEGATIVE_TTL + 1
        with mock.patch.object(lookups.time, 'time', return_value=later):
            models = [missing()]
            lookups.attach_cached(models)
        self.assertEqual(models[0]['lookup_state'], 'pending')

    def test_switched_off_queues_nothing(self):
        settings.save_settings({'auto_lookup': False})
        models = [missing()]
        self.assertEqual(lookups.attach_cached(models), 0)
        self.assertEqual(models[0]['lookup_state'], 'idle')
        lookups._worker.wait_idle(5)
        self.assertEqual(self.calls, [])

    def test_models_with_a_perfect_local_match_are_not_looked_up(self):
        models = [missing(matches=[{'confidence': 100.0, 'category_mismatch': False}])]
        lookups.attach_cached(models)
        self.assertEqual(models[0]['lookup_state'], 'not_needed')
        lookups._worker.wait_idle(5)
        self.assertEqual(self.calls, [])

    def test_duplicate_references_are_looked_up_once(self):
        self.release.clear()
        models = [missing(), missing(), missing('sub/flux1-dev-fp8.safetensors')]
        self.assertEqual(lookups.attach_cached(models), 3)
        self.release.set()
        lookups._worker.wait_idle(5)
        self.assertEqual(len(self.calls), 1)

    def test_a_failure_is_not_remembered(self):
        request = lookups._request_for(missing())
        request['raise'] = True
        lookups._worker.enqueue('k', request, force=True)
        lookups._worker.wait_idle(5)
        self.assertIsNone(lookups._store.get('k'))
        self.assertFalse(lookups._worker.is_pending('k'))

    def test_refresh_asks_again_even_when_switched_off(self):
        settings.save_settings({'auto_lookup': False})
        lookups.attach_cached([missing()])
        key = lookups.refresh(missing())
        lookups._worker.wait_idle(5)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(lookups.results_for([key])[key]['state'], 'not_found')

    def test_untrusted_request_fields_are_sanitised(self):
        request = lookups._request_for(missing('../../x.safetensors', source_url='javascript:alert(1)',
                                               source_hash='nothex', source_hash_type='sha256'))
        self.assertEqual(request['filename'], 'x.safetensors')
        self.assertEqual(request['workflow_url'], '')
        self.assertEqual(request['sha256'], '')
```

Append to `tests/test_source_chain.py` (Task 2's file, which already skips without `requests`):

```python
class InProcessCacheTests(unittest.TestCase):
    """A background lookup must really ask again, not replay an in-memory miss."""

    def test_lookup_ignores_misses_cached_by_the_sources(self):
        from core import lookups
        from core.sources import civarchive, civitai, huggingface

        huggingface._search_cache['hf_x.safetensors_exactTrue'] = None
        civitai._search_cache['civit_x.safetensors_exactTrue'] = None
        civarchive._cache[SHA] = None
        seen = {}

        def chain(filename, **kwargs):
            seen['huggingface'] = 'hf_x.safetensors_exactTrue' in huggingface._search_cache
            seen['civitai'] = 'civit_x.safetensors_exactTrue' in civitai._search_cache
            seen['civarchive'] = SHA in civarchive._cache
            return None

        with mock.patch.object(source_chain, 'find_download_source', chain):
            lookups._find({'filename': 'x.safetensors', 'category': 'loras', 'workflow_url': '', 'sha256': SHA})
        self.assertEqual(seen, {'huggingface': False, 'civitai': False, 'civarchive': False})
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m unittest discover -t . -s tests -p "test_lookups.py" -v` and `... -p "test_source_chain.py" -v`
Expected: ERROR — `No module named 'core.lookups'` (both).

- [ ] **Step 3: Implement `core/lookups.py`**

```python
"""
Background lookups of download sources for missing models.

Analysis stays local and fast: it attaches whatever earlier lookups found and
queues the rest. One worker thread asks the source chain, so a slow or
unreachable service delays only that answer - never the dialog, never ComfyUI.
Answers persist in data/lookups.json, so a lookup that finishes after the
dialog was closed is waiting there the next time it opens.
"""

import json
import logging
import os
import queue
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from . import overrides, settings
from .categories import canonical_category

logger = logging.getLogger(__name__)

STORE_FILENAME = 'lookups.json'
# The sources swallow network errors and answer "nothing found", so a negative
# answer may only mean the network was down. Ask again after a day.
NEGATIVE_TTL = 24 * 3600

_SHA256 = re.compile(r'^[0-9a-f]{64}$')


def _basename(path: str) -> str:
    return re.split(r'[/\\]', path or '')[-1]


def _sha256_of(missing: Dict[str, Any]) -> str:
    value = str(missing.get('source_hash') or '').strip().lower()
    hash_type = str(missing.get('source_hash_type') or 'sha256').strip().lower()
    return value if hash_type == 'sha256' and _SHA256.match(value) else ''


def lookup_key(missing: Dict[str, Any]) -> str:
    """Identity of what is being looked for: its hash when known, else category and name."""
    sha256 = _sha256_of(missing)
    if sha256:
        return f'sha256:{sha256}'
    category = canonical_category(missing.get('category')) or 'unknown'
    return f'{category}:{_basename(missing.get("original_path")).lower()}'


def _request_for(missing: Dict[str, Any]) -> Dict[str, str]:
    """What the source chain is asked, built only from fields that are safe to send."""
    url = missing.get('source_url') or ''
    try:
        if urlparse(url).scheme != 'https':
            url = ''
    except ValueError:
        url = ''
    category = missing.get('category')
    return {
        'filename': _basename(missing.get('original_path')),
        'category': category if canonical_category(category) else '',
        'workflow_url': url,
        'sha256': _sha256_of(missing),
    }


def _has_perfect_match(missing: Dict[str, Any]) -> bool:
    return any(m.get('confidence') == 100 and not m.get('category_mismatch')
               for m in missing.get('matches') or [])


class LookupStore:
    """Answers by key, persisted as one JSON document."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data: Optional[Dict[str, Dict[str, Any]]] = None

    def _path(self) -> str:
        return os.path.join(overrides._data_dir(), STORE_FILENAME)

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if self._data is None:
            try:
                with open(self._path(), 'r', encoding='utf-8') as handle:
                    data = json.load(handle)
                self._data = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                self._data = {}
        return self._data

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """The stored answer, or None when unknown or a negative answer has expired."""
        with self._lock:
            entry = self._load().get(key)
        if not isinstance(entry, dict):
            return None
        if entry.get('result') is None and time.time() - float(entry.get('checked_at') or 0) > NEGATIVE_TTL:
            return None
        return entry

    def put(self, key: str, result: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            data = self._load()
            data[key] = {'result': result, 'checked_at': time.time()}
            path = self._path()
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, indent=1)
            os.replace(tmp, path)


class LookupWorker:
    """One daemon thread working through queued lookups, one key at a time."""

    def __init__(self, store: LookupStore, find: Callable[[Dict[str, str]], Optional[Dict[str, Any]]]):
        self.store = store
        self.find = find
        self._queue: 'queue.Queue[str]' = queue.Queue()
        self._pending: Dict[str, Dict[str, str]] = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def is_pending(self, key: str) -> bool:
        with self._lock:
            return key in self._pending

    def enqueue(self, key: str, request: Dict[str, str], force: bool = False) -> bool:
        """Queue a lookup. Returns False if one for this key is already queued."""
        with self._lock:
            if key in self._pending:
                return False
            if not force and self.store.get(key) is not None:
                return False
            self._pending[key] = request
            self._queue.put(key)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name='model-linker-lookups', daemon=True)
                self._thread.start()
        return True

    def wait_idle(self, timeout: float) -> None:
        """Block until the queue is empty (tests only)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if not self._pending:
                    return
            time.sleep(0.01)

    def _run(self) -> None:
        while True:
            key = self._queue.get()
            with self._lock:
                request = self._pending.get(key)
            try:
                if request is not None:
                    result = self.find(request)
                    self.store.put(key, result)
            except Exception as e:
                # Not stored: the next analysis asks again
                logger.warning(f"Model Linker: lookup for {request and request.get('filename')} failed: {e}")
            finally:
                with self._lock:
                    self._pending.pop(key, None)
                self._queue.task_done()


def _forget_source_caches(filename: str, sha256: str) -> None:
    """
    The ported sources memoise misses in-process until restart (HuggingFace
    even after a 429). The store above is the cache lookups rely on, and it
    only sends work here when it holds no valid answer - so ask afresh.
    """
    from .sources import civarchive, civitai, huggingface
    for exact in (True, False):
        huggingface._search_cache.pop(f'hf_{filename}_exact{exact}', None)
        civitai._search_cache.pop(f'civit_{filename}_exact{exact}', None)
    if sha256:
        civarchive._cache.pop(sha256.lower(), None)


def _find(request: Dict[str, str]) -> Optional[Dict[str, Any]]:
    from .source_chain import find_download_source
    _forget_source_caches(request['filename'], request.get('sha256') or '')
    return find_download_source(
        request['filename'],
        category=request.get('category') or '',
        workflow_url=request.get('workflow_url') or '',
        sha256=request.get('sha256') or '',
    )


_store = LookupStore()
_worker: Optional[LookupWorker] = None


def worker() -> LookupWorker:
    global _worker
    if _worker is None:
        _worker = LookupWorker(_store, _find)
    return _worker


def attach_cached(missing_models: List[Dict[str, Any]]) -> int:
    """Annotate each missing model with what is known, and queue what is not."""
    auto = settings.get_option('auto_lookup')
    pending = 0
    for missing in missing_models:
        key = lookup_key(missing)
        missing['lookup_key'] = key
        if _has_perfect_match(missing):
            missing['lookup_state'] = 'not_needed'
            continue
        entry = _store.get(key)
        if entry is not None:
            missing['download_source'] = entry.get('result')
            missing['lookup_state'] = 'found' if entry.get('result') else 'not_found'
        elif worker().is_pending(key) or (auto and worker().enqueue(key, _request_for(missing))):
            missing['lookup_state'] = 'pending'
            pending += 1
        else:
            missing['lookup_state'] = 'idle'
    return pending


def results_for(keys: List[str]) -> Dict[str, Dict[str, Any]]:
    results = {}
    for key in keys:
        if worker().is_pending(key):
            results[key] = {'state': 'pending', 'source': None}
            continue
        entry = _store.get(key)
        if entry is None:
            results[key] = {'state': 'unknown', 'source': None}
        else:
            results[key] = {'state': 'found' if entry.get('result') else 'not_found',
                            'source': entry.get('result')}
    return results


def refresh(missing: Dict[str, Any]) -> str:
    """Ask again now, whatever the store says - the user pressed the button."""
    key = lookup_key(missing)
    worker().enqueue(key, _request_for(missing), force=True)
    return key
```

- [ ] **Step 4: Run the lookup tests**

Run: `python -m unittest discover -t . -s tests -p "test_lookups.py" -v` and `... -p "test_source_chain.py" -v`
Expected: PASS.

- [ ] **Step 5: Add the routes and wire `/analyze`**

Create `core/routes_downloads.py`:

```python
"""
Routes for background lookups and downloads. Registered only when `requests`
imports - see __init__.setup_routes.
"""

import asyncio
import logging

from . import lookups

logger = logging.getLogger(__name__)

MAX_KEYS = 500


def _bad(web, message):
    return web.json_response({'error': message}, status=400)


def register(routes, web) -> None:
    @routes.post("/model_linker/lookups")
    async def lookup_results(request):
        try:
            data = await request.json()
        except Exception:
            return _bad(web, 'Body must be JSON')
        keys = data.get('keys') if isinstance(data, dict) else None
        if not isinstance(keys, list) or len(keys) > MAX_KEYS:
            return _bad(web, f'keys must be a list of at most {MAX_KEYS} strings')
        keys = [k for k in keys if isinstance(k, str)]
        results = await asyncio.to_thread(lookups.results_for, keys)
        return web.json_response({'results': results})

    @routes.post("/model_linker/lookups/refresh")
    async def lookup_refresh(request):
        try:
            data = await request.json()
        except Exception:
            return _bad(web, 'Body must be JSON')
        missing = data.get('missing') if isinstance(data, dict) else None
        if not isinstance(missing, dict) or not isinstance(missing.get('original_path'), str):
            return _bad(web, 'missing.original_path is required')
        fields = ('original_path', 'category', 'source_url', 'source_hash', 'source_hash_type')
        clean = {k: missing[k] for k in fields if isinstance(missing.get(k), str)}
        key = await asyncio.to_thread(lookups.refresh, clean)
        return web.json_response({'key': key, 'state': 'pending'})
```

In `__init__.py`, inside `setup_routes`, directly after the `except ImportError` that guards the core imports (before `@routes.post("/model_linker/analyze")`), add:

```python
            # Downloads and online lookups need `requests`; without it the
            # extension still relinks models from disk. Any other failure in
            # the download stack is a bug - logged in full, but it must never
            # take relinking (/analyze, /resolve) down with it.
            try:
                import requests  # noqa: F401
                from .core import lookups, routes_downloads
                routes_downloads.register(routes, web)
                lookups_available = True
            except Exception as e:
                lookups = None
                lookups_available = False
                if isinstance(e, ImportError):
                    self.logger.warning(f"Model Linker: downloads and online lookups are disabled ({e})")
                else:
                    self.logger.error(f"Model Linker: downloads and online lookups failed to load: {e}",
                                      exc_info=True)
```

In the `/analyze` handler, after `result = await asyncio.to_thread(analyze_and_find_matches, workflow_json)`, add:

```python
                    if lookups_available:
                        # Only reads the store and queues work; never waits on the network
                        result['lookups_pending'] = await asyncio.to_thread(
                            lookups.attach_cached, result['missing_models'])
```

- [ ] **Step 6: Run the whole suite**

Run: `python tests/run.py`
Expected: all pass.

- [ ] **Step 7: Verify in the app**

Restart ComfyUI. Load a workflow with a missing `flux1-dev-fp8.safetensors` (a `CheckpointLoaderSimple` referencing it). Open the dialog: the browser's Network tab shows `/model_linker/analyze` answering in well under a second with `lookups_pending: 1`. Run `curl -s -X POST http://127.0.0.1:8188/model_linker/lookups -H "Content-Type: application/json" -d '{"keys":["checkpoints:flux1-dev-fp8.safetensors"]}'` a few seconds later: `state` is `found`. Check `data/lookups.json` exists and is not listed by `git status`.

- [ ] **Step 8: Commit**

```bash
git add core/lookups.py core/routes_downloads.py __init__.py tests/test_lookups.py tests/test_source_chain.py
git commit -m "Look up download sources in the background and remember the answers"
```

---

### Task 4: Download queue

**Files:**
- Create: `core/downloader.py`
- Modify: `core/routes_downloads.py` (download, progress and cancel routes)
- Test: `tests/test_downloader.py` (new)

**Interfaces:**
- Consumes: `settings.auth_headers_for_url`, `settings.provider_for_url`, `categories.canonical_category`, `reveal._is_within(path, base) -> bool`, `scanner.MODEL_EXTENSIONS`.
- Produces (Tasks 5–7 rely on these exact names):
  - Module state used by the ported HF engine: `download_progress: Dict[str, dict]`, `download_lock`, `cancelled_downloads: set`, `format_bytes(n) -> str`, `generate_download_id() -> str`.
  - `start_download(url, category, filename, expected_sha256=None, expected_size=None) -> str` (download id); raises `DownloadError(message)`.
  - `get_progress(id) -> dict`, `get_all_progress() -> {id: dict}`, `cancel_download(id) -> bool`.
  - `add_completion_hook(fn: Callable[[dict], Optional[str]])` — called on a separate thread with the finished record (`path, category, sha256, filename, url`); a returned string is stored as the record's `metadata` field.
  - `sha256_file(path, on_progress=None) -> str`.
  - Progress record keys: `status` (`queued|starting|downloading|verifying|completed|error|cancelled`), `progress` (0–100), `total_size`, `downloaded`, `speed`, `filename`, `url`, `error`, `start_time`, `category`, `path`, and on success `sha256`; on auth failure `auth_required` (`'huggingface'|'civitai'`); after hooks `metadata`.
  - Routes: `POST /model_linker/download` `{url, category, filename, sha256?, size?}` → `{download_id}` or 400 `{error}`; `GET /model_linker/progress` → `{id: record}`; `GET /model_linker/progress/{id}` → record or 404; `POST /model_linker/cancel/{id}` → `{success}`.

A record is **finished** only when `status == 'completed'` **and** it has `sha256` — the HuggingFace engine (Task 5) reports `completed` before this module has verified the file.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_downloader.py`:

```python
"""
Downloads: where files may land, what is sent where, and that nothing
model-named is left behind by a failure.
"""

import hashlib
import os
import threading
import unittest
from unittest import mock

try:
    import requests
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import downloader, overrides, settings
from support import LibraryTestCase

BODY = b'model weights' * 1000


class FakeResponse:
    def __init__(self, body=BODY, status_code=200, length=None):
        self.body = body
        self.status_code = status_code
        self.headers = {'content-length': str(len(body) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        for i in range(0, len(self.body), 4096):
            yield self.body[i:i + 4096]


class DownloaderTestCase(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.loras = self.add_models('loras', [], ['.safetensors'])
        self.sent = []
        # Tokens are read from settings.json; never the developer's real one
        data_dir = os.path.join(self.root, 'data')
        os.makedirs(data_dir, exist_ok=True)
        for patcher in (mock.patch.object(downloader, '_ensure_workers'),
                        mock.patch.object(overrides, '_data_dir', lambda: data_dir)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def respond(self, response):
        def fake_get(url, headers=None, **kwargs):
            self.sent.append((url, dict(headers or {})))
            return response
        patcher = mock.patch.object(requests, 'get', fake_get)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_download(self, url='https://civitai.com/api/download/models/1', filename='new.safetensors', **kw):
        download_id = downloader.start_download(url, 'loras', filename, **kw)
        downloader._run(download_id)
        return downloader.get_progress(download_id)


class DestinationTests(DownloaderTestCase):
    def test_only_https(self):
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('http://civitai.com/x', 'loras', 'a.safetensors')

    def test_folders_in_the_name_are_dropped(self):
        self.respond(FakeResponse())
        record = self.run_download(filename='..\\..\\escape.safetensors')
        self.assertEqual(record['status'], 'completed')
        self.assertTrue(os.path.isfile(os.path.join(self.loras, 'escape.safetensors')))

    def test_rejects_drive_relative_and_hidden_names(self):
        # On Windows basename('C:evil.safetensors') is 'evil.safetensors', so
        # the colon must be refused in the name as given
        for name in ('C:evil.safetensors', 'a:b.safetensors', '.hidden.safetensors', '', 'x.exe'):
            with self.assertRaises(downloader.DownloadError, msg=name):
                downloader.start_download('https://civitai.com/x', 'loras', name)

    def test_rejects_unknown_category(self):
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('https://civitai.com/x', 'no_such_category', 'a.safetensors')

    def test_never_overwrites(self):
        with open(os.path.join(self.loras, 'have.safetensors'), 'wb') as handle:
            handle.write(b'x')
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('https://civitai.com/x', 'loras', 'have.safetensors')

    def test_refuses_a_second_download_of_the_same_file(self):
        downloader.start_download('https://civitai.com/x', 'loras', 'twice.safetensors')
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('https://civitai.com/y', 'loras', 'twice.safetensors')


class TransferTests(DownloaderTestCase):
    def test_streams_then_renames_and_hashes(self):
        self.respond(FakeResponse())
        record = self.run_download()
        path = os.path.join(self.loras, 'new.safetensors')
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['sha256'], hashlib.sha256(BODY).hexdigest())
        with open(path, 'rb') as handle:
            self.assertEqual(handle.read(), BODY)
        self.assertFalse(os.path.exists(path + '.part'))

    def test_short_body_leaves_nothing(self):
        self.respond(FakeResponse(length=len(BODY) * 4))
        record = self.run_download()
        self.assertEqual(record['status'], 'error')
        self.assertEqual(os.listdir(self.loras), [])

    def test_file_appearing_mid_download_is_not_overwritten(self):
        dest = os.path.join(self.loras, 'new.safetensors')

        class Racing(FakeResponse):
            def iter_content(self, size):
                for i, chunk in enumerate(super().iter_content(size)):
                    if i == 1:
                        with open(dest, 'wb') as handle:
                            handle.write(b'theirs')
                    yield chunk

        self.respond(Racing())
        record = self.run_download()
        self.assertEqual(record['status'], 'error')
        with open(dest, 'rb') as handle:
            self.assertEqual(handle.read(), b'theirs')
        self.assertFalse(os.path.exists(dest + '.part'))

    def test_hash_mismatch_deletes_the_file(self):
        self.respond(FakeResponse())
        record = self.run_download(expected_sha256='00' * 32)
        self.assertEqual(record['status'], 'error')
        self.assertEqual(os.listdir(self.loras), [])

    def test_rejected_login_names_the_provider(self):
        self.respond(FakeResponse(status_code=401))
        record = self.run_download()
        self.assertEqual(record['status'], 'error')
        self.assertEqual(record['auth_required'], 'civitai')
        self.assertIn('Settings', record['error'])

    def test_token_goes_only_to_the_real_host(self):
        with mock.patch.object(settings, 'get_token', return_value='secret-token'):
            self.respond(FakeResponse())
            self.run_download(url='https://civitai.com.evil.example/x', filename='a.safetensors')
            self.run_download(url='https://civitai.com/api/download/models/1', filename='b.safetensors')
        self.assertNotIn('Authorization', self.sent[0][1])
        self.assertEqual(self.sent[1][1].get('Authorization'), 'Bearer secret-token')

    def test_not_enough_disk_space(self):
        self.respond(FakeResponse())
        usage = mock.Mock(free=10)
        with mock.patch.object(downloader.shutil, 'disk_usage', return_value=usage):
            record = self.run_download(expected_size=len(BODY))
        self.assertEqual(record['status'], 'error')
        self.assertIn('disk space', record['error'])

    def test_cancel_while_queued(self):
        download_id = downloader.start_download('https://civitai.com/x', 'loras', 'c.safetensors')
        self.assertTrue(downloader.cancel_download(download_id))
        downloader._run(download_id)
        self.assertEqual(downloader.get_progress(download_id)['status'], 'cancelled')
        self.assertEqual(self.sent, [])

    def test_completion_hook_receives_the_record(self):
        seen = threading.Event()
        received = {}

        def hook(record):
            received.update(record)
            seen.set()
            return 'written'

        downloader.add_completion_hook(hook)
        self.addCleanup(downloader._completion_hooks.remove, hook)
        self.respond(FakeResponse())
        record = self.run_download(filename='hooked.safetensors')
        self.assertTrue(seen.wait(5))
        self.assertEqual(received['sha256'], record['sha256'])
        self.assertEqual(received['category'], 'loras')
        self.assertTrue(received['path'].endswith('hooked.safetensors'))
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m unittest discover -t . -s tests -p "test_downloader.py" -v`
Expected: ERROR — `cannot import name 'downloader' from 'core'`.

- [ ] **Step 3: Implement `core/downloader.py`**

```python
"""
Downloads into ComfyUI's model folders.

Downloads run server-side in a small queue, so they carry on when the dialog
closes and "download all" cannot open twenty connections at once. A file
streams to `<name>.part` and only takes its real name once complete and
hashed, so ComfyUI never lists a half-written model.

`download_progress`, `download_lock`, `cancelled_downloads`, `format_bytes`
and `generate_download_id` are the interface the HuggingFace engine
(`hf_downloader.py`, ported from upstream) expects. Keep their names.
"""

import hashlib
import importlib
import logging
import os
import queue
import shutil
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from . import settings
from .categories import canonical_category
from .reveal import _is_within
from .scanner import MODEL_EXTENSIONS

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
HASH_CHUNK_SIZE = 8 * 1024 * 1024
MAX_WORKERS = 2
# Left free beyond the file itself, so a download never fills the disk
DISK_HEADROOM = 512 * 1024 * 1024
ACTIVE_STATES = ('queued', 'starting', 'downloading', 'verifying')

download_progress: Dict[str, Dict[str, Any]] = {}
download_lock = threading.Lock()
cancelled_downloads: set = set()

_queue: 'queue.Queue[str]' = queue.Queue()
_jobs: Dict[str, Dict[str, Any]] = {}
_workers: List[threading.Thread] = []
_completion_hooks: List[Callable[[Dict[str, Any]], Optional[str]]] = []


class DownloadError(Exception):
    """A download that cannot start or did not finish, worded for the user."""

    def __init__(self, message: str, auth_required: Optional[str] = None):
        super().__init__(message)
        self.auth_required = auth_required


class _Cancelled(Exception):
    pass


def format_bytes(size: Any) -> str:
    size = float(size or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def generate_download_id() -> str:
    return uuid.uuid4().hex[:8]


def category_directory(category: str) -> Optional[str]:
    """First directory registered for a category, trying its canonical name too."""
    import folder_paths
    names = [category] if category else []
    canonical = canonical_category(category)
    if canonical and canonical not in names:
        names.append(canonical)
    for name in names:
        try:
            paths = folder_paths.get_folder_paths(name)
        except Exception:
            continue
        if paths:
            return paths[0]
    return None


def resolve_destination(category: str, filename: str) -> str:
    """Where a download may be written, or DownloadError."""
    # Checked on the name as given: on Windows basename('C:evil.x') is 'evil.x',
    # so a colon would be gone before a check on the basename could see it
    if ':' in (filename or ''):
        raise DownloadError(f'Invalid filename: {filename!r}')
    name = (filename or '').replace('\\', '/').rsplit('/', 1)[-1]
    if not name or name.startswith('.'):
        raise DownloadError(f'Invalid filename: {filename!r}')
    if os.path.splitext(name)[1].lower() not in MODEL_EXTENSIONS:
        raise DownloadError(f'Not a model file: {name}')
    base = category_directory(category)
    if not base:
        raise DownloadError(f'No model folder is registered for "{category}"')
    dest = os.path.join(base, name)
    if not _is_within(dest, base):
        raise DownloadError('Destination escapes the model folder')
    return dest


def _set(download_id: str, **fields: Any) -> None:
    with download_lock:
        record = download_progress.get(download_id)
        if record is not None:
            record.update(fields)


def get_progress(download_id: str) -> Dict[str, Any]:
    with download_lock:
        return dict(download_progress.get(download_id) or {})


def get_all_progress() -> Dict[str, Dict[str, Any]]:
    with download_lock:
        return {k: dict(v) for k, v in download_progress.items()}


def cancel_download(download_id: str) -> bool:
    with download_lock:
        record = download_progress.get(download_id)
        if record is None:
            return False
        if record.get('status') == 'queued':
            record['status'] = 'cancelled'
        elif record.get('status') in ACTIVE_STATES:
            cancelled_downloads.add(download_id)
    return True


def add_completion_hook(hook: Callable[[Dict[str, Any]], Optional[str]]) -> None:
    if hook not in _completion_hooks:
        _completion_hooks.append(hook)


def start_download(url: str, category: str, filename: str,
                   expected_sha256: Optional[str] = None,
                   expected_size: Optional[int] = None) -> str:
    parsed = urlparse(url or '')
    if parsed.scheme != 'https' or not parsed.hostname:
        raise DownloadError('Only https:// download links are accepted')
    dest = resolve_destination(category, filename)
    if os.path.exists(dest):
        raise DownloadError(f'{os.path.basename(dest)} already exists in {os.path.dirname(dest)}')

    with download_lock:
        for record in download_progress.values():
            if record.get('path') == dest and record.get('status') in ACTIVE_STATES:
                raise DownloadError(f'{os.path.basename(dest)} is already downloading')
        download_id = generate_download_id()
        download_progress[download_id] = {
            'status': 'queued', 'progress': 0, 'total_size': int(expected_size or 0),
            'downloaded': 0, 'speed': 0, 'filename': os.path.basename(dest), 'url': url,
            'error': None, 'start_time': None, 'category': category, 'path': dest,
        }
        _jobs[download_id] = {
            'url': url, 'category': category, 'dest': dest,
            'expected_sha256': (expected_sha256 or '').lower() or None,
            'expected_size': int(expected_size or 0) or None,
        }
    _ensure_workers()
    _queue.put(download_id)
    return download_id


def _ensure_workers() -> None:
    with download_lock:
        _workers[:] = [t for t in _workers if t.is_alive()]
        while len(_workers) < MAX_WORKERS:
            thread = threading.Thread(target=_worker_loop, daemon=True,
                                      name=f'model-linker-download-{len(_workers)}')
            _workers.append(thread)
            thread.start()


def _worker_loop() -> None:
    while True:
        download_id = _queue.get()
        try:
            _run(download_id)
        except Exception:
            logger.exception(f"Model Linker: download {download_id} failed unexpectedly")
            _set(download_id, status='error', error='Unexpected error - see the ComfyUI log', speed=0)


def _remove(path: str) -> None:
    # Windows can hold a just-closed file for a moment
    for _ in range(10):
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.2)


def _check_disk(directory: str, size: Optional[int]) -> None:
    if not size:
        return
    os.makedirs(directory, exist_ok=True)
    free = shutil.disk_usage(directory).free
    if free < size + DISK_HEADROOM:
        raise DownloadError(f'Not enough disk space: {format_bytes(size)} needed, '
                            f'{format_bytes(free)} free in {directory}')


def _auth_message(provider: Optional[str], had_token: bool, status: int) -> str:
    name = {'huggingface': 'HuggingFace', 'civitai': 'CivitAI'}.get(provider or '')
    if not name:
        return f'The server requires a login ({status})'
    if had_token:
        return (f"{name} rejected the saved token ({status}). Check it in ⚙ Settings, "
                f"and accept the model's terms on its page if it is gated.")
    return f'{name} requires a token for this file ({status}). Add one in ⚙ Settings.'


def sha256_file(path: str, on_progress: Optional[Callable[[int, int], None]] = None) -> str:
    digest = hashlib.sha256()
    total = os.path.getsize(path)
    done = 0
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(HASH_CHUNK_SIZE), b''):
            digest.update(block)
            done += len(block)
            if on_progress:
                on_progress(done, total)
    return digest.hexdigest()


def _stream(download_id: str, job: Dict[str, Any]) -> str:
    """Plain HTTPS download, hashed as it arrives. Returns the SHA-256."""
    import requests

    url, dest = job['url'], job['dest']
    part = dest + '.part'
    digest = hashlib.sha256()
    headers = settings.auth_headers_for_url(url)
    provider = settings.provider_for_url(url)
    try:
        # requests drops Authorization when a redirect leaves the host
        with requests.get(url, headers=headers, stream=True, timeout=30, allow_redirects=True) as response:
            if response.status_code in (401, 403):
                raise DownloadError(_auth_message(provider, bool(headers), response.status_code),
                                    auth_required=provider)
            if response.status_code == 404:
                raise DownloadError('The file is no longer at this address (404)')
            if response.status_code >= 400:
                raise DownloadError(f'The server refused the download ({response.status_code})')

            total = int(response.headers.get('content-length') or 0)
            _check_disk(os.path.dirname(dest), total)
            _set(download_id, status='downloading', total_size=total or job['expected_size'] or 0)

            done, last_time, last_done = 0, time.time(), 0
            with open(part, 'wb') as handle:
                for chunk in response.iter_content(CHUNK_SIZE):
                    if download_id in cancelled_downloads:
                        raise _Cancelled()
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last_time >= 0.5:
                        _set(download_id, downloaded=done,
                             speed=int((done - last_done) / (now - last_time)),
                             progress=int(done * 100 / total) if total else 0)
                        last_time, last_done = now, done
            _set(download_id, downloaded=done)
        if total and done != total:
            raise DownloadError('The connection closed before the file was complete')
        if os.path.exists(dest):
            # Someone else put a file there meanwhile; never overwrite it
            raise DownloadError(f'{os.path.basename(dest)} appeared during the download; not overwritten')
        os.replace(part, dest)
        return digest.hexdigest()
    except requests.RequestException as e:
        raise DownloadError(f'Network error: {e}')
    finally:
        if os.path.exists(part):
            _remove(part)


def _hf_engine_for(url: str):
    """(hf_downloader module, parsed url) when the HuggingFace engine should take this URL."""
    if settings.provider_for_url(url) != 'huggingface':
        return None
    try:
        # Through importlib, not `from . import`: that form finds the package
        # attribute set by an earlier import and never consults sys.modules
        hf_downloader = importlib.import_module('.hf_downloader', __package__)
    except ImportError:
        return None
    from .sources.huggingface import parse_huggingface_url
    parsed = parse_huggingface_url(url)
    return (hf_downloader, parsed) if parsed else None


def _run_hf(download_id: str, job: Dict[str, Any], engine) -> str:
    """HuggingFace engine (Task 5). Returns the SHA-256 of the file it wrote."""
    hf, parsed = engine
    dest = job['dest']
    hf.download_hf_model(parsed['repo'], parsed['filename'], os.path.dirname(dest),
                         os.path.basename(dest), download_id,
                         revision=parsed.get('branch', 'main'), display_url=job['url'])
    # The engine updates the record in place (Task 5), so `path` - which the
    # duplicate-download guard in start_download relies on - survived the run.
    # Re-assert what this module owns in case an older engine replaced it.
    _set(download_id, category=job['category'], path=dest)
    record = get_progress(download_id)
    if record.get('status') == 'cancelled':
        raise _Cancelled()
    if record.get('status') != 'completed':
        raise DownloadError(record.get('error') or 'Download failed',
                            auth_required=record.get('auth_required'))
    _set(download_id, status='verifying', speed=0, progress=0)
    return sha256_file(dest, lambda d, t: _set(download_id, progress=int(d * 100 / t) if t else 100))


def _run(download_id: str) -> None:
    job = _jobs.pop(download_id, None)
    if job is None or get_progress(download_id).get('status') == 'cancelled':
        return
    dest = job['dest']
    try:
        _check_disk(os.path.dirname(dest), job['expected_size'])
        if os.path.exists(dest):
            raise DownloadError(f'{os.path.basename(dest)} appeared while queued; not overwritten')
        _set(download_id, status='starting', start_time=time.time())
        engine = _hf_engine_for(job['url'])
        sha256 = _run_hf(download_id, job, engine) if engine else _stream(download_id, job)
        if job['expected_sha256'] and sha256 != job['expected_sha256']:
            _remove(dest)
            raise DownloadError('The downloaded file does not match the expected hash and was deleted')
        _set(download_id, status='completed', progress=100, speed=0, sha256=sha256)
    except _Cancelled:
        with download_lock:
            cancelled_downloads.discard(download_id)
        _set(download_id, status='cancelled', speed=0)
        return
    except DownloadError as e:
        fields = {'status': 'error', 'error': str(e), 'speed': 0}
        if e.auth_required:
            fields['auth_required'] = e.auth_required
        _set(download_id, **fields)
        return
    _run_hooks(download_id)


def _run_hooks(download_id: str) -> None:
    record = get_progress(download_id)

    def run():
        for hook in list(_completion_hooks):
            try:
                outcome = hook(record)
                if outcome:
                    _set(download_id, metadata=outcome)
            except Exception:
                logger.exception('Model Linker: after-download step failed')
                _set(download_id, metadata='failed')

    threading.Thread(target=run, name='model-linker-after-download', daemon=True).start()
```

- [ ] **Step 4: Run the downloader tests**

Run: `python -m unittest discover -t . -s tests -p "test_downloader.py" -v`
Expected: PASS. (`test_cancel_while_queued` relies on `_run` returning before any request; `test_refuses_a_second_download_of_the_same_file` leaves a queued record — harmless, records are per-process.)

- [ ] **Step 5: Add the routes**

Append inside `register` in `core/routes_downloads.py` (and add `from . import downloader` to its imports):

```python
    @routes.post("/model_linker/download")
    async def start(request):
        try:
            data = await request.json()
        except Exception:
            return _bad(web, 'Body must be JSON')
        if not isinstance(data, dict):
            return _bad(web, 'Body must be an object')
        url, category, filename = data.get('url'), data.get('category'), data.get('filename')
        if not all(isinstance(v, str) and v for v in (url, category, filename)):
            return _bad(web, 'url, category and filename are required')
        sha256 = data.get('sha256') if isinstance(data.get('sha256'), str) else None
        size = data.get('size') if isinstance(data.get('size'), int) and data.get('size') > 0 else None
        try:
            download_id = await asyncio.to_thread(downloader.start_download, url, category, filename, sha256, size)
        except downloader.DownloadError as e:
            return _bad(web, str(e))
        return web.json_response({'download_id': download_id})

    @routes.get("/model_linker/progress")
    async def all_progress(request):
        return web.json_response(downloader.get_all_progress())

    @routes.get("/model_linker/progress/{download_id}")
    async def one_progress(request):
        record = downloader.get_progress(request.match_info['download_id'])
        return web.json_response(record) if record else web.json_response({'error': 'Unknown download'}, status=404)

    @routes.post("/model_linker/cancel/{download_id}")
    async def cancel(request):
        return web.json_response({'success': downloader.cancel_download(request.match_info['download_id'])})
```

- [ ] **Step 6: Run the whole suite and commit**

Run: `python tests/run.py` — expected: all pass.

```bash
git add core/downloader.py core/routes_downloads.py tests/test_downloader.py
git commit -m "Download models through a server-side queue into validated folders"
```

---

### Task 5: HuggingFace engine (multi-connection downloads)

**Files:**
- Create (ported): `core/hf_downloader.py`, `core/_hf_worker.py`
- Test: `tests/test_hf_routing.py` (new)

**Interfaces:**
- Consumes: `downloader.download_progress`, `download_lock`, `cancelled_downloads`, `format_bytes`, `generate_download_id` (Task 4); `settings.get_token('huggingface')`.
- Produces: `hf_downloader.download_hf_model(repo_id, filename, dest_dir, dest_filename, download_id, revision='main', display_url=None) -> None` — blocking; updates the shared progress record **in place** (keeping `path` and `category`, so `start_download`'s duplicate guard holds while it runs); never renames over a file that appeared meanwhile; flattens the repo path to `dest_dir/dest_filename`; stages in `dest_dir/.model_linker_tmp_<id>/` (the scanner skips dot-directories). Importing it raises `ImportError` when `huggingface_hub` is missing or older than 1.1, which makes `downloader._hf_engine_for` fall back to streaming.

- [ ] **Step 1: Copy, then edit**

```bash
git show bec5c80:core/hf_downloader.py > core/hf_downloader.py
git show bec5c80:core/_hf_worker.py > core/_hf_worker.py
```

It imports exactly the five names Task 4's `downloader` exports. Three edits to `core/hf_downloader.py`:

1. **Update the record, do not replace it.** Upstream assigns a fresh dict, which drops the `path` and `category` that `downloader.start_download` uses to refuse a second download of the same file — so during every HuggingFace download (the main source) that guard was blind. Replace

```python
    with download_lock:
        download_progress[download_id] = {
            'status': 'starting',
```

with

```python
    with download_lock:
        # In place: the record also carries downloader's `path` and `category`
        download_progress.setdefault(download_id, {}).update({
            'status': 'starting',
```

and the dict's closing line directly below it (the `        }` after `'start_time': start_time,`) with `        })`.

2. **Never rename over a file that appeared meanwhile.** In the success branch, directly before `os.replace(result_path, dest_path)`, add (same indentation; the surrounding `except Exception` turns it into an error record and removes the staging folder):

```python
            if os.path.exists(dest_path):
                raise FileExistsError(f"{dest_filename} appeared during the download; not overwritten")
```

3. Its `start_background_hf_download` is unused here — delete that function (the last one in the file) so no second, uncapped way of starting downloads exists.

- [ ] **Step 2: Write the tests**

Create `tests/test_hf_routing.py`:

```python
"""Which engine takes a URL - decided by the real host."""

import unittest
from unittest import mock

try:
    import requests  # noqa: F401
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import downloader
from support import LibraryTestCase


class RoutingTests(unittest.TestCase):
    def test_lookalike_host_streams(self):
        self.assertIsNone(downloader._hf_engine_for(
            'https://huggingface.co.evil.example/a/b/resolve/main/c.safetensors'))

    def test_civitai_streams(self):
        self.assertIsNone(downloader._hf_engine_for('https://civitai.com/api/download/models/1'))

    def test_real_huggingface_uses_the_engine_when_available(self):
        try:
            import core.hf_downloader  # noqa: F401
        except ImportError:
            self.skipTest('huggingface_hub >= 1.1 is not installed')
        engine = downloader._hf_engine_for('https://huggingface.co/Comfy-Org/x/resolve/main/sub/c.safetensors')
        self.assertIsNotNone(engine)
        self.assertEqual(engine[1]['filename'], 'sub/c.safetensors')

    def test_missing_library_falls_back(self):
        # Even after the engine was imported once in this process - the case
        # a plain `from . import hf_downloader` gets wrong
        try:
            import core.hf_downloader  # noqa: F401
        except ImportError:
            pass
        with mock.patch.dict('sys.modules', {'core.hf_downloader': None}):
            self.assertIsNone(downloader._hf_engine_for(
                'https://huggingface.co/Comfy-Org/x/resolve/main/c.safetensors'))


class RecordTests(LibraryTestCase):
    """The engine shares downloader's progress record and must not drop its fields."""

    def setUp(self):
        super().setUp()
        try:
            from core import hf_downloader
        except ImportError:
            self.skipTest('huggingface_hub >= 1.1 is not installed')
        self.hf = hf_downloader
        self.loras = self.add_models('loras', [], ['.safetensors'])
        for patcher in (mock.patch.object(downloader, '_ensure_workers'),
                        mock.patch.object(hf_downloader, 'get_token', return_value=None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_duplicate_guard_holds_while_the_engine_runs(self):
        url = 'https://huggingface.co/o/r/resolve/main/x.safetensors'
        download_id = downloader.start_download(url, 'loras', 'x.safetensors')
        refused = []

        def popen(*args, **kwargs):
            # The engine has written its 'starting' record by now
            try:
                downloader.start_download(url, 'loras', 'x.safetensors')
            except downloader.DownloadError:
                refused.append(True)
            raise OSError('no worker process in tests')

        with mock.patch.object(self.hf.subprocess, 'Popen', popen):
            self.hf.download_hf_model('o/r', 'x.safetensors', self.loras, 'x.safetensors', download_id)
        self.assertEqual(refused, [True])
        record = downloader.get_progress(download_id)
        self.assertEqual(record['status'], 'error')
        self.assertEqual(record['category'], 'loras')
        self.assertTrue(record['path'].endswith('x.safetensors'))
```

- [ ] **Step 3: Run the engine tests**

Run: `python -m unittest discover -t . -s tests -p "test_hf_routing.py" -v`
Expected: PASS, none skipped. (Without Step 1's edit 1, `test_duplicate_guard_holds_while_the_engine_runs` fails with `[] != [True]` — the replaced record has no `path`. That is the regression it pins; check it once by reverting the edit if in doubt.)

- [ ] **Step 4: Run the whole suite**

Run: `python tests/run.py`
Expected: all pass.

- [ ] **Step 5: Verify in the app**

Restart ComfyUI. Start a download of any public Comfy-Org file from the dialog (Task 7 adds the button; until then: `curl -s -X POST http://127.0.0.1:8188/model_linker/download -H "Content-Type: application/json" -d '{"url":"https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive/resolve/main/v1-5-pruned-emaonly-fp16.safetensors","category":"checkpoints","filename":"v1-5-pruned-emaonly-fp16.safetensors"}'`). Poll `/model_linker/progress`: it moves `starting → downloading → verifying → completed` with a `sha256`, and the console logs `[Model Linker] ✓ Download complete`. While it runs, send the same `curl` again: it must answer 400 `… is already downloading`. Delete the file afterwards.

- [ ] **Step 6: Commit**

```bash
git add core/hf_downloader.py core/_hf_worker.py tests/test_hf_routing.py
git commit -m "Download HuggingFace files over multiple connections when huggingface_hub allows"
```

---

### Task 6: Lora Manager sidecars and previews for downloaded models

**Files:**
- Create: `core/civitai_meta.py`, `core/lm_sidecar.py`
- Modify: `core/routes_downloads.py` (register the completion hook)
- Test: `tests/test_lm_sidecar.py` (new)

**Interfaces:**
- Consumes: `downloader.add_completion_hook`, `settings.auth_headers_for_url`, `categories.canonical_category`.
- Produces:
  - `civitai_meta.version_for_sha256(sha256) -> Optional[dict]` — full CivitAI version record, enriched from `/models/{modelId}`, `images[].meta.comfy` removed; None when CivitAI does not know the file; raises `civitai_meta.CivitaiUnavailable` on network/HTTP failure.
  - `lm_sidecar.build_sidecar(model_path, category, sha256, version=None, preview_path='', preview_nsfw_level=0, now=None) -> dict`
  - `lm_sidecar.write_sidecar(model_path, category, sha256, fetch_if_unknown=True) -> str` — `'written' | 'exists' | 'skipped'`. `fetch_if_unknown=False` skips asking Lora Manager to fetch from CivitAI when CivitAI did not know the file (Task 9 passes it for HuggingFace downloads, which are hydrated instead).
  - `lm_sidecar.on_download_complete(record) -> Optional[str]` — the hook.
  - `lm_sidecar.determine_base_model(base: Optional[str]) -> str`, `lm_sidecar.lm_target(category) -> Optional[(prefix, sub_type)]`.
  - `lm_sidecar.lora_manager_settings_path() -> str` — where Lora Manager keeps `settings.json`, resolved in Lora Manager's own order: `LORA_MANAGER_SETTINGS_DIR`; else its folder when portable (`LORA_MANAGER_PORTABLE=1`, or `use_portable_settings: true` there unless `LORA_MANAGER_PORTABLE=0`); else the per-user config folder (`%LOCALAPPDATA%\ComfyUI-LoRA-Manager` on Windows). `centralized_sidecars()` reads that file.
  - `lm_sidecar.notify_lora_manager(prefix, model_path, fetch=False)` scans only the model's folder (`?roots=`), falling back to a full scan when Lora Manager does not know that root.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lm_sidecar.py`:

```python
"""
Sidecars Lora Manager reads as its own. A sidecar missing a required key makes
Lora Manager skip the model entirely, so the required set is checked against
Lora Manager's published schema whenever it is installed alongside.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from core import civitai_meta, lm_sidecar

SHA = 'ab' * 32
LM_REQUIRED = ('file_name', 'model_name', 'file_path', 'size', 'modified', 'sha256', 'base_model', 'preview_url')
SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.pardir,
                      'comfyui-lora-manager', '.specs', 'metadata.schema.json')

VERSION = {
    'id': 3090220, 'modelId': 2224836, 'name': 'v1', 'baseModel': 'SDXL Lightning', 'trainedWords': ['zz'],
    'model': {'name': 'Rebel', 'type': 'LORA', 'description': '<p>d</p>', 'tags': ['style']},
    'files': [
        {'name': 'other.safetensors', 'hashes': {'SHA256': 'CD' * 32, 'AutoV3': '111111111111AA'}},
        {'name': 'rebel.safetensors', 'hashes': {'SHA256': SHA.upper(), 'AutoV3': 'F7AD278A79A8FF'}},
    ],
    'images': [
        {'url': 'https://image.civitai.com/x/abc/original=true/1.jpeg', 'nsfwLevel': 8, 'type': 'image'},
        {'url': 'https://image.civitai.com/x/def/original=true/2.jpeg', 'nsfwLevel': 1, 'type': 'image',
         'meta': {'prompt': 'p', 'comfy': '{huge}'}},
    ],
}


class SidecarTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='model-linker-lm-')
        self.model = os.path.join(self.root, 'rebel.safetensors')
        with open(self.model, 'wb') as handle:
            handle.write(b'weights')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class FormatTests(SidecarTestCase):
    def test_required_keys_and_types(self):
        data = lm_sidecar.build_sidecar(self.model, 'loras', SHA.upper(), VERSION, now=123.0)
        for key in LM_REQUIRED:
            self.assertIn(key, data)
        self.assertEqual(data['file_name'], 'rebel')
        self.assertEqual(data['model_name'], 'Rebel')
        self.assertEqual(data['sha256'], SHA)
        self.assertIsInstance(data['size'], int)
        self.assertIsInstance(data['modified'], float)
        self.assertNotIn('\\', data['file_path'])
        self.assertTrue(os.path.isabs(data['file_path'].replace('/', os.sep)))
        self.assertEqual(data['metadata_source'], 'civitai_api')
        self.assertTrue(data['from_civitai'])
        self.assertEqual(data['hash_status'], 'completed')
        self.assertEqual(data['tags'], ['style'])
        self.assertEqual(data['modelDescription'], '<p>d</p>')

    def test_type_specific_fields(self):
        lora = lm_sidecar.build_sidecar(self.model, 'loras', SHA, VERSION)
        self.assertEqual(lora['usage_tips'], '{}')
        self.assertNotIn('sub_type', lora)
        for category, sub_type in (('checkpoints', 'checkpoint'), ('diffusion_models', 'diffusion_model'),
                                   ('unet', 'diffusion_model'), ('embeddings', 'embedding'),
                                   ('vae', 'vae'), ('clip', 'text_encoder')):
            data = lm_sidecar.build_sidecar(self.model, category, SHA, VERSION)
            self.assertEqual(data['sub_type'], sub_type, category)
            self.assertNotIn('usage_tips', data, category)
            self.assertNotIn('model_type', data, category)

    def test_autov3_three_states(self):
        self.assertEqual(lm_sidecar.build_sidecar(self.model, 'loras', SHA, VERSION)['autov3'], 'f7ad278a79a8')
        unmatched = dict(VERSION, files=[])
        self.assertIn('autov3', lm_sidecar.build_sidecar(self.model, 'loras', SHA, unmatched))
        self.assertIsNone(lm_sidecar.build_sidecar(self.model, 'loras', SHA, unmatched)['autov3'])
        self.assertNotIn('autov3', lm_sidecar.build_sidecar(self.model, 'loras', SHA, None))

    def test_unknown_to_civitai(self):
        data = lm_sidecar.build_sidecar(self.model, 'loras', SHA, None)
        self.assertFalse(data['from_civitai'])
        self.assertEqual(data['civitai'], {})
        self.assertIsNone(data['metadata_source'])
        self.assertEqual(data['base_model'], 'Unknown')
        self.assertEqual(data['model_name'], 'rebel')

    def test_base_model_mapping_matches_lora_manager(self):
        cases = {None: 'Unknown', '': 'Unknown', 'SDXL 1.0': 'SDXL 1.0', 'SDXL Lightning': 'SDXL 1.0',
                 'Pony V7': 'Pony', 'Illustrious': 'Illustrious', 'Flux.1 D': 'Flux.1 D',
                 'Flux.1 Krea': 'Flux.1 Krea', 'SD 1.5': 'SD 1.5', 'ZImageTurbo': 'ZImageTurbo',
                 # Lora Manager's "Hunyuan Video" key is never matched (it lowercases
                 # the input only), so the string is kept as CivitAI gave it
                 'Hunyuan Video 1.5': 'Hunyuan Video 1.5'}
        for given, expected in cases.items():
            self.assertEqual(lm_sidecar.determine_base_model(given), expected, given)

    def test_unmanaged_categories_get_nothing(self):
        self.assertIsNone(lm_sidecar.lm_target('style_models'))
        self.assertEqual(lm_sidecar.write_sidecar(self.model, 'style_models', SHA), 'skipped')

    @unittest.skipUnless(os.path.isfile(SCHEMA), 'Lora Manager is not installed alongside')
    def test_satisfies_lora_managers_published_schema(self):
        with open(SCHEMA, encoding='utf-8') as handle:
            schema = json.load(handle)
        branches = {b['title']: b for b in schema['oneOf']}
        types = {'string': str, 'integer': int, 'number': (int, float), 'boolean': bool,
                 'array': list, 'object': dict, 'null': type(None)}
        for title, category in (('LoRA Model Metadata', 'loras'), ('Checkpoint Model Metadata', 'checkpoints'),
                                ('Embedding Model Metadata', 'embeddings')):
            data = lm_sidecar.build_sidecar(self.model, category, SHA, VERSION)
            branch = branches[title]
            for key in branch.get('required', []):
                self.assertIn(key, data, f'{title}: {key}')
            for key, spec in branch.get('properties', {}).items():
                if key not in data or 'type' not in spec:
                    continue
                allowed = spec['type'] if isinstance(spec['type'], list) else [spec['type']]
                ok = any(isinstance(data[key], types[t]) and not (t in ('integer', 'number') and isinstance(data[key], bool))
                         for t in allowed)
                self.assertTrue(ok, f'{title}: {key}={data[key]!r} is not {allowed}')


class WriteTests(SidecarTestCase):
    def setUp(self):
        super().setUp()
        self.notified = []
        patches = [
            mock.patch.object(lm_sidecar, 'notify_lora_manager',
                              lambda prefix, path, fetch=False: self.notified.append((prefix, fetch))),
            mock.patch.object(lm_sidecar, 'centralized_sidecars', return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def read(self):
        with open(os.path.join(self.root, 'rebel.metadata.json'), encoding='utf-8') as handle:
            return json.load(handle)

    def test_writes_sidecar_and_preview(self):
        def fake_preview(model_path, image):
            path = os.path.splitext(model_path)[0] + '.jpeg'
            with open(path, 'wb') as handle:
                handle.write(b'jpg')
            return path

        with mock.patch.object(civitai_meta, 'version_for_sha256', return_value=VERSION), \
             mock.patch.object(lm_sidecar, 'download_preview', side_effect=fake_preview) as preview:
            self.assertEqual(lm_sidecar.write_sidecar(self.model, 'loras', SHA), 'written')
        data = self.read()
        self.assertTrue(data['preview_url'].endswith('/rebel.jpeg'))
        self.assertEqual(data['preview_nsfw_level'], 1)  # the safe image was chosen
        self.assertEqual(preview.call_args[0][1]['nsfwLevel'], 1)
        self.assertEqual(self.notified, [('loras', False)])

    def test_civitai_down_still_writes_a_loadable_sidecar(self):
        with mock.patch.object(civitai_meta, 'version_for_sha256',
                               side_effect=civitai_meta.CivitaiUnavailable('timeout')):
            self.assertEqual(lm_sidecar.write_sidecar(self.model, 'loras', SHA), 'written')
        data = self.read()
        for key in LM_REQUIRED:
            self.assertIn(key, data)
        self.assertFalse(data['from_civitai'])
        self.assertEqual(self.notified, [('loras', True)])  # Lora Manager is asked to fetch it itself

    def test_never_overwrites_an_existing_sidecar(self):
        path = os.path.join(self.root, 'rebel.metadata.json')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('{"favorite": true}')
        self.assertEqual(lm_sidecar.write_sidecar(self.model, 'loras', SHA), 'exists')
        with open(path, encoding='utf-8') as handle:
            self.assertEqual(handle.read(), '{"favorite": true}')

    def test_centralized_mode_is_left_alone(self):
        with mock.patch.object(lm_sidecar, 'centralized_sidecars', return_value=True):
            self.assertEqual(lm_sidecar.write_sidecar(self.model, 'loras', SHA), 'skipped')

    def test_no_civitai_fetch_when_told_not_to(self):
        with mock.patch.object(civitai_meta, 'version_for_sha256', return_value=None):
            lm_sidecar.write_sidecar(self.model, 'loras', SHA, fetch_if_unknown=False)
        self.assertEqual(self.notified, [('loras', False)])


class LoraManagerSettingsTests(unittest.TestCase):
    """settings.json is found where Lora Manager itself looks for it."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='model-linker-lm-settings-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.lm_dir = os.path.join(self.root, 'comfyui-lora-manager')
        self.user_dir = os.path.join(self.root, 'user')
        os.makedirs(self.lm_dir)
        os.makedirs(self.user_dir)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in ('LORA_MANAGER_SETTINGS_DIR', 'LORA_MANAGER_PORTABLE'):
            os.environ.pop(name, None)
        for patcher in (mock.patch.object(lm_sidecar, '_LORA_MANAGER_DIR', self.lm_dir),
                        mock.patch.object(lm_sidecar, '_user_config_dir', return_value=self.user_dir)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, folder, data):
        with open(os.path.join(folder, 'settings.json'), 'w', encoding='utf-8') as handle:
            json.dump(data, handle)

    def test_per_user_folder_unless_portable(self):
        self.write(self.lm_dir, {'sidecar_storage_mode': 'centralized'})  # a stale copy
        self.write(self.user_dir, {'sidecar_storage_mode': 'alongside'})
        self.assertEqual(lm_sidecar.lora_manager_settings_path(), os.path.join(self.user_dir, 'settings.json'))
        self.assertFalse(lm_sidecar.centralized_sidecars())

    def test_portable_mode_uses_its_own_folder(self):
        self.write(self.lm_dir, {'use_portable_settings': True, 'sidecar_storage_mode': 'centralized'})
        self.assertTrue(lm_sidecar.centralized_sidecars())
        os.environ['LORA_MANAGER_PORTABLE'] = '0'  # explicit opt-out wins over the file
        self.assertEqual(lm_sidecar.lora_manager_settings_path(), os.path.join(self.user_dir, 'settings.json'))

    def test_settings_dir_override_wins(self):
        self.write(self.lm_dir, {'use_portable_settings': True})
        os.environ['LORA_MANAGER_SETTINGS_DIR'] = self.root
        self.assertEqual(lm_sidecar.lora_manager_settings_path(), os.path.join(self.root, 'settings.json'))


class CivitaiMetaTests(unittest.TestCase):
    def test_enrichment_and_comfy_metadata_removal(self):
        model = {'description': 'full', 'tags': ['a'], 'creator': {'username': 'u'},
                 'allowNoCredit': True, 'allowCommercialUse': ['Image']}
        version = civitai_meta.strip_comfy_metadata(civitai_meta.enrich_version(VERSION, model))
        self.assertEqual(version['model']['description'], 'full')
        self.assertEqual(version['model']['tags'], ['a'])
        self.assertEqual(version['model']['allowCommercialUse'], ['Image'])
        self.assertEqual(version['creator'], {'username': 'u'})
        self.assertNotIn('comfy', version['images'][1]['meta'])
        self.assertIn('comfy', VERSION['images'][1]['meta'])  # the input is not mutated
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m unittest discover -t . -s tests -p "test_lm_sidecar.py" -v`
Expected: ERROR — `cannot import name 'civitai_meta' from 'core'`.

- [ ] **Step 3: Implement `core/civitai_meta.py`**

```python
"""
CivitAI's full record for a file, shaped the way Lora Manager stores it.

`sources/civitai.py` reduces a by-hash answer to a download link. A Lora
Manager sidecar needs the whole version record, enriched with what only the
model endpoint carries - tags, description, creator, licence flags. Lora
Manager never re-fetches once `civitai.id` is set, so a thin record would stay
thin for good.
"""

import copy
import logging
from typing import Any, Dict, Optional

from . import settings

logger = logging.getLogger(__name__)

API = 'https://civitai.com/api/v1'
_LICENCE_FIELDS = ('allowNoCredit', 'allowCommercialUse', 'allowDerivatives', 'allowDifferentLicense')


class CivitaiUnavailable(Exception):
    """CivitAI could not be asked - unlike None, which means it does not know the file."""


def _get_json(url: str) -> Optional[Dict[str, Any]]:
    import requests
    try:
        response = requests.get(url, headers=settings.auth_headers_for_url(url), timeout=20)
    except requests.RequestException as e:
        raise CivitaiUnavailable(str(e))
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise CivitaiUnavailable(f'HTTP {response.status_code} from {url}')
    try:
        data = response.json()
    except ValueError:
        raise CivitaiUnavailable(f'Not JSON from {url}')
    return data if isinstance(data, dict) else None


def enrich_version(version: Dict[str, Any], model: Dict[str, Any]) -> Dict[str, Any]:
    """Copy of `version` with the model-level details Lora Manager reads."""
    version = copy.deepcopy(version)
    info = version.get('model') if isinstance(version.get('model'), dict) else {}
    version['model'] = info
    info['description'] = model.get('description')
    info['tags'] = model.get('tags') or []
    for field in _LICENCE_FIELDS:
        if field in model:
            info[field] = model[field]
    if model.get('creator'):
        version['creator'] = model['creator']
    return version


def strip_comfy_metadata(version: Dict[str, Any]) -> Dict[str, Any]:
    """Drop embedded ComfyUI graphs from image metadata - large, and of no use in a sidecar."""
    for image in version.get('images') or []:
        meta = image.get('meta') if isinstance(image, dict) else None
        if isinstance(meta, dict):
            meta.pop('comfy', None)
    return version


def version_for_sha256(sha256: str) -> Optional[Dict[str, Any]]:
    version = _get_json(f'{API}/model-versions/by-hash/{sha256.lower()}')
    if not version or not version.get('id'):
        return None
    model_id = version.get('modelId')
    if model_id:
        model = _get_json(f'{API}/models/{model_id}')
        if model:
            version = enrich_version(version, model)
    return strip_comfy_metadata(copy.deepcopy(version))
```

(`enrich_version` already deep-copies; the extra copy in the no-model path keeps the "input is not mutated" guarantee uniform.)

- [ ] **Step 4: Implement `core/lm_sidecar.py`**

```python
"""
Model info in Lora Manager's sidecar format, written beside each download.

Lora Manager (GPL-3.0) is not imported or copied from: this writes the file
format its documentation defines - `<stem>.metadata.json` plus a preview
image - so it finds the model already described and never needs a manual
fetch. Contract: docs/superpowers/specs/2026-10-14-upstream-parity-design.md.
"""

import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from . import civitai_meta
from .categories import canonical_category

logger = logging.getLogger(__name__)

_EXTENSION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LORA_MANAGER_DIR = os.path.join(os.path.dirname(_EXTENSION_DIR), 'comfyui-lora-manager')
_LORA_MANAGER_APP = 'ComfyUI-LoRA-Manager'

# Canonical ComfyUI category -> (Lora Manager route prefix, sub_type)
_TARGETS = {
    'loras': ('loras', None),
    'checkpoints': ('checkpoints', 'checkpoint'),
    'diffusion_models': ('checkpoints', 'diffusion_model'),
    'embeddings': ('embeddings', 'embedding'),
    'vae': ('other', 'vae'),
    'upscale_models': ('other', 'upscaler'),
    'text_encoders': ('other', 'text_encoder'),
    'clip_vision': ('other', 'clip_vision'),
    'controlnet': ('other', 'controlnet'),
}

# Lora Manager's base-model labels: the first case-insensitive substring hit
# wins, otherwise CivitAI's string is kept. Order matters ("il" comes after
# "illustrious" in theirs, so it does here). Lora Manager also lists a
# "Hunyuan Video" key, but compares it against the lowercased input, so it can
# never match - leaving it out reproduces its behaviour exactly.
_BASE_MODELS = (
    ('sd_1.5', 'SD 1.5'), ('sd-v1-5', 'SD 1.5'), ('sd-v2-1', 'SD 2.1'), ('sdxl', 'SDXL 1.0'),
    ('sd-v2', 'SD 2.0'), ('flux1', 'Flux.1 D'), ('flux.1 d', 'Flux.1 D'), ('illustrious', 'Illustrious'),
    ('il', 'Illustrious'), ('pony', 'Pony'),
)

# Where Lora Manager looks for a preview, in its order of preference
PREVIEW_EXTENSIONS = ('.webp', '.preview.webp', '.preview.png', '.preview.jpeg', '.preview.jpg',
                      '.preview.mp4', '.png', '.jpeg', '.jpg', '.mp4', '.gif', '.webm', '.avif', '.jxl')
_SAVEABLE_PREVIEW = {'.jpeg', '.jpg', '.png', '.webp', '.gif'}
MAX_PREVIEW_BYTES = 25 * 1024 * 1024


def lm_target(category: Optional[str]) -> Optional[Tuple[str, Optional[str]]]:
    return _TARGETS.get(canonical_category(category) or '')


def determine_base_model(base: Optional[str]) -> str:
    if not base:
        return 'Unknown'
    lowered = base.lower()
    for needle, label in _BASE_MODELS:
        if needle in lowered:
            return label
    return base


def lm_path(path: str) -> str:
    """Absolute, forward slashes, links NOT resolved - the form Lora Manager keys on."""
    return os.path.abspath(path).replace('\\', '/')


def sidecar_path(model_path: str) -> str:
    return os.path.splitext(model_path)[0] + '.metadata.json'


def autov3_for(version: Dict[str, Any], sha256: str) -> Optional[str]:
    for entry in version.get('files') or []:
        hashes = entry.get('hashes') or {}
        if str(hashes.get('SHA256', '')).lower() == sha256.lower():
            value = hashes.get('AutoV3')
            # e3b0c44298fc is the hash of empty input - a packaging artefact
            if isinstance(value, str) and len(value) >= 12 and value[:12].lower() != 'e3b0c44298fc':
                return value[:12].lower()
            return None
    return None


def build_sidecar(model_path: str, category: str, sha256: str,
                  version: Optional[Dict[str, Any]] = None, preview_path: str = '',
                  preview_nsfw_level: int = 0, now: Optional[float] = None) -> Dict[str, Any]:
    now = float(time.time() if now is None else now)
    stem = os.path.splitext(os.path.basename(model_path))[0]
    model_info = (version or {}).get('model') or {}
    data: Dict[str, Any] = {
        'file_name': stem,
        'model_name': model_info.get('name') or stem,
        'file_path': lm_path(model_path),
        'size': int(os.path.getsize(model_path)),
        'modified': now,
        'sha256': sha256.lower(),
        'base_model': determine_base_model((version or {}).get('baseModel')),
        'preview_url': lm_path(preview_path) if preview_path else '',
        'preview_nsfw_level': int(preview_nsfw_level or 0),
        'notes': '',
        'from_civitai': bool(version),
        'civitai': version or {},
        'tags': list(model_info.get('tags') or []),
        'modelDescription': model_info.get('description') or '',
        'civitai_deleted': False,
        'favorite': False,
        'exclude': False,
        'db_checked': False,
        'skip_metadata_refresh': False,
        'metadata_source': 'civitai_api' if version else None,
        'last_checked_at': now if version else 0.0,
        'hash_status': 'completed',
    }
    if version:
        # Present-but-null means "checked, CivitAI has none"; absent means "not checked"
        data['autov3'] = autov3_for(version, sha256)
    prefix, sub_type = lm_target(category) or ('loras', None)
    if prefix == 'loras':
        data['usage_tips'] = '{}'
    elif sub_type:
        data['sub_type'] = sub_type
    return data


def existing_preview(model_path: str) -> Optional[str]:
    base = os.path.splitext(model_path)[0]
    for ext in PREVIEW_EXTENSIONS:
        if os.path.isfile(base + ext):
            return base + ext
    return None


def pick_preview(version: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """First still image, preferring one below CivitAI's R level (4)."""
    images = [i for i in version.get('images') or [] if isinstance(i, dict) and i.get('url')]
    stills = [i for i in images if (i.get('type') or 'image') == 'image']
    tame = [i for i in stills if int(i.get('nsfwLevel') or 0) < 4]
    return (tame or stills or [None])[0]


def _rendition(url: str) -> str:
    return url.replace('/original=true/', '/width=450,optimized=true/')


def download_preview(model_path: str, image: Dict[str, Any]) -> str:
    """Save CivitAI's 450px rendition as `<stem><ext>`. Returns the path, or '' on any failure."""
    import requests
    url = _rendition(image.get('url') or '')
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not (host == 'civitai.com' or host.endswith('.civitai.com')):
        return ''
    ext = os.path.splitext(parsed.path)[1].lower()
    if ext not in _SAVEABLE_PREVIEW:
        return ''
    dest = os.path.splitext(model_path)[0] + ext
    tmp = dest + '.part'
    try:
        with requests.get(url, stream=True, timeout=30) as response:
            if response.status_code != 200:
                return ''
            size = 0
            with open(tmp, 'wb') as handle:
                for chunk in response.iter_content(256 * 1024):
                    size += len(chunk)
                    if size > MAX_PREVIEW_BYTES:
                        raise ValueError('preview too large')
                    handle.write(chunk)
        os.replace(tmp, dest)
        return dest
    except Exception as e:
        logger.debug(f"Model Linker: preview download failed: {e}")
        return ''
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _user_config_dir() -> str:
    """The per-user folder Lora Manager keeps its settings in when not portable."""
    try:
        from platformdirs import user_config_dir  # a Lora Manager dependency
        return user_config_dir(_LORA_MANAGER_APP, appauthor=False)
    except ImportError:
        if os.name == 'nt':
            return os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), _LORA_MANAGER_APP)
        if sys.platform == 'darwin':
            return os.path.join(os.path.expanduser('~/Library/Application Support'), _LORA_MANAGER_APP)
        return os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'),
                            _LORA_MANAGER_APP)


def _portable(project_settings: str) -> bool:
    forced = os.environ.get('LORA_MANAGER_PORTABLE')
    if forced in ('0', '1'):
        return forced == '1'
    try:
        with open(project_settings, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get('use_portable_settings') is True


def lora_manager_settings_path() -> str:
    """
    Lora Manager's settings.json, looked for in the order Lora Manager uses:
    the LORA_MANAGER_SETTINGS_DIR override, then its own folder when portable
    mode is on, then the per-user config folder. A settings.json left in its
    folder without the portable flag is a stale copy, not the live file.
    """
    override = os.environ.get('LORA_MANAGER_SETTINGS_DIR')
    if override:
        return os.path.join(os.path.abspath(os.path.expanduser(override)), 'settings.json')
    project = os.path.join(_LORA_MANAGER_DIR, 'settings.json')
    if _portable(project):
        return project
    return os.path.join(_user_config_dir(), 'settings.json')


def centralized_sidecars() -> bool:
    """Lora Manager's optional centralized mode keeps sidecars elsewhere; leave it alone then."""
    try:
        with open(lora_manager_settings_path(), 'r', encoding='utf-8') as handle:
            return json.load(handle).get('sidecar_storage_mode') == 'centralized'
    except (OSError, ValueError, AttributeError):
        return False


def _write_json_atomic(path: str, data: Dict[str, Any]) -> None:
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def notify_lora_manager(prefix: str, model_path: str, fetch: bool = False) -> None:
    """
    Best effort: have a running Lora Manager index the new file now, and fetch
    its info itself when CivitAI could not be asked (its own chain also tries
    CivArchive). Silently does nothing when Lora Manager is not installed.
    """
    try:
        import requests
        from server import PromptServer
        port = PromptServer.instance.port
    except Exception:
        return
    base = f'http://127.0.0.1:{port}/api/lm/{prefix}'
    try:
        # Only the folder the download landed in (a category root). Lora
        # Manager answers 400 for a root it spells differently; scan all then.
        response = requests.get(f'{base}/scan', params={'roots': lm_path(os.path.dirname(model_path))},
                                timeout=300)
        if response.status_code == 400:
            requests.get(f'{base}/scan', timeout=300)
        if fetch:
            requests.post(f'{base}/fetch-civitai', json={'file_path': lm_path(model_path)}, timeout=300)
    except Exception as e:
        logger.debug(f"Model Linker: Lora Manager not notified: {e}")


def write_sidecar(model_path: str, category: str, sha256: str, fetch_if_unknown: bool = True) -> str:
    target = lm_target(category)
    if not target:
        return 'skipped'
    if centralized_sidecars():
        logger.info('Model Linker: Lora Manager keeps sidecars centrally; not writing one beside the model')
        return 'skipped'
    path = sidecar_path(model_path)
    if os.path.exists(path):
        return 'exists'

    try:
        version = civitai_meta.version_for_sha256(sha256)
    except civitai_meta.CivitaiUnavailable as e:
        logger.warning(f"Model Linker: CivitAI unavailable, writing basic model info: {e}")
        version = None

    preview, nsfw = existing_preview(model_path) or '', 0
    if version and not preview:
        image = pick_preview(version)
        if image:
            preview = download_preview(model_path, image)
            nsfw = int(image.get('nsfwLevel') or 0) if preview else 0

    _write_json_atomic(path, build_sidecar(model_path, category, sha256, version, preview, nsfw))
    notify_lora_manager(target[0], model_path, fetch=version is None and fetch_if_unknown)
    return 'written'


def on_download_complete(record: Dict[str, Any]) -> Optional[str]:
    if not record.get('sha256') or not record.get('path'):
        return None
    return write_sidecar(record['path'], record.get('category'), record['sha256'])
```

- [ ] **Step 5: Run the sidecar tests**

Run: `python -m unittest discover -t . -s tests -p "test_lm_sidecar.py" -v`
Expected: PASS, including `test_satisfies_lora_managers_published_schema` (not skipped on this machine).

- [ ] **Step 6: Register the hook**

In `core/routes_downloads.py`, at the top of `register`, add:

```python
    from . import lm_sidecar
    downloader.add_completion_hook(lm_sidecar.on_download_complete)
```

- [ ] **Step 7: Run the whole suite**

Run: `python tests/run.py`
Expected: all pass.

- [ ] **Step 8: Verify against a running Lora Manager**

Restart ComfyUI. Download a small public CivitAI LoRA through `/model_linker/download` (`"category": "loras"`). When the record shows `metadata: "written"`:
1. `<stem>.metadata.json` and `<stem>.jpeg` sit beside the file.
2. Open Lora Manager: the LoRA appears without pressing Refresh, with its preview, base model, trigger words and the CivitAI link.
3. Lora Manager's log shows no `Parse error in metadata file` for it.
Then delete the three files.

- [ ] **Step 9: Commit**

```bash
git add core/civitai_meta.py core/lm_sidecar.py core/routes_downloads.py tests/test_lm_sidecar.py
git commit -m "Write Lora Manager model info and previews beside downloaded models"
```

---

### Task 7: Frontend — lookups, downloads and progress

**Files:**
- Create: `web/modules/downloads.js`
- Modify: `web/modules/linker-dialog.js` (constructor, `createFooter`, `close`, `renderMissingModel`, `displayMissingModels`, header settings button → `openSettings()`)
- Test: `tests/js/test_downloads.mjs` (new)

**Interfaces:**
- Consumes: `/analyze` fields `lookup_key`, `lookup_state`, `download_source`, `lookups_pending`; routes from Tasks 3–4; `isPerfectMatch`, `refSlot`, `escapeHtml`, `safeHttpUrl`, `notifyError` from `util.js`; `dialog.loadWorkflowData()`.
- Produces:
  - `downloadState(missing, record) -> {kind: 'active'|'done'|'error'|'none'|'available'|'searching'|'not_found'|'idle', ...}` (pure, exported).
  - `isFinished(record) -> boolean` (exported).
  - `DownloadController` with `renderBlock(missing) -> string`, `afterRender(container, data)`, `downloadAll()`, `stop()`.
  - `LinkerManagerDialog.openSettings()`.

- [ ] **Step 1: Write the failing test**

Create `tests/js/test_downloads.mjs`:

```js
// What the download block shows, and the rules that keep it cheap and safe:
// results patch cards in place, one download per file, nothing unescaped.
import { createChecker, loadModule, readSource } from './harness.mjs';

export const name = 'downloads';

export default async function run() {
  const check = createChecker(name);
  const { downloadState, isFinished } = await loadModule('modules/downloads.js');

  const https = { url: 'https://huggingface.co/a/b/resolve/main/x.safetensors', source: 'huggingface' };
  check('a found https source is downloadable',
        downloadState({ download_source: https, lookup_state: 'found' }).kind === 'available');
  check('an http source is not',
        downloadState({ download_source: { url: 'http://x/y' }, lookup_state: 'found' }).kind !== 'available');
  check('a javascript: source is not',
        downloadState({ download_source: { url: 'javascript:alert(1)' }, lookup_state: 'found' }).kind !== 'available');
  check('a perfect local match needs no download',
        downloadState({ matches: [{ confidence: 100 }], download_source: https }).kind === 'none');
  check('a wrong-folder 100% match still offers the download',
        downloadState({ matches: [{ confidence: 100, category_mismatch: true }], download_source: https }).kind === 'available');
  check('pending lookups show as searching', downloadState({ lookup_state: 'pending' }).kind === 'searching');
  check('a running download wins', downloadState({ download_source: https }, { status: 'downloading' }).kind === 'active');
  check('HF "completed" before verification is still active',
        downloadState({}, { status: 'completed' }).kind === 'active' && !isFinished({ status: 'completed' }));
  check('completed with a hash is finished', isFinished({ status: 'completed', sha256: 'ab' }));

  const src = readSource('modules/downloads.js');
  const poll = src.slice(src.indexOf('async pollLookups'), src.indexOf('async searchAgain'));
  check('lookup results patch cards in place', /this\.refresh\(/.test(poll));
  check('lookup results never re-render the list',
        !/displayMissingModels|loadWorkflowData|innerHTML\s*=\s*[^;]*missing/.test(poll));
  check('download all starts one download per file', /seen\.has\(/.test(src.slice(src.indexOf('async downloadAll'))));
  check('reopening reconnects to running downloads', /async reconnect\(/.test(src) && /reconnect\(\)/.test(src));
  check('source names are escaped', /escapeHtml\(where\)/.test(src));
  check('workflow-derived slots are escaped in attributes',
        !/data-ml-slot="\$\{slot\}"/.test(src) && /data-ml-slot="\$\{escapeHtml\(slot\)\}"/.test(src)
        && /id="\$\{escapeHtml\(this\.blockId\(missing\)\)\}"/.test(src) && /CSS\.escape\(/.test(src));

  const dialog = readSource('modules/linker-dialog.js');
  check('closing the dialog stops polling', /close\(\)\s*\{[\s\S]{0,600}downloads\.stop\(\)/.test(dialog));
  check('the footer offers download all', /downloadAll\(\)/.test(dialog));
  return check.failures;
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `node tests/js/run.mjs`
Expected: FAIL — `modules/downloads.js` does not exist.

- [ ] **Step 3: Implement `web/modules/downloads.js`**

```js
/**
 * Downloads for missing models: what the background lookups found, starting
 * downloads, and their progress. Downloads run on the server, so closing the
 * dialog does not stop them; reopening it picks their progress back up.
 */

import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

import { escapeHtml, isPerfectMatch, notifyError, refSlot, safeHttpUrl } from "./util.js";

const ACTIVE = new Set(["queued", "starting", "downloading", "verifying"]);

const SOURCE_LABELS = {
    workflow: "the workflow's own link",
    hf_index: "HuggingFace (Comfy-Org / Kijai)",
    civitai_hash: "CivitAI, by hash",
    civarchive: "CivArchive, by hash",
    popular: "the curated list",
    model_list: "ComfyUI-Manager's list",
    huggingface: "HuggingFace",
    civitai: "CivitAI",
};

const basename = (path) => (path || "").split(/[\\/]/).pop();
const fileKey = (m) => `${m.category}|${basename(m.original_path).toLowerCase()}`;

export function formatBytes(n) {
    if (typeof n !== "number" || !isFinite(n) || n <= 0) return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

/** Finished means completed AND verified - the HF engine says "completed" before hashing. */
export function isFinished(record) {
    return record?.status === "completed" && !!record.sha256;
}

/** What a missing model's download block should show. Pure. */
export function downloadState(missing, record) {
    if (record && (ACTIVE.has(record.status) || (record.status === "completed" && !record.sha256))) {
        return { kind: "active" };
    }
    if (isFinished(record)) return { kind: "done" };
    if (record?.status === "error") return { kind: "error", message: record.error, auth: record.auth_required };
    if ((missing?.matches || []).some(isPerfectMatch)) return { kind: "none" };
    const url = safeHttpUrl(missing?.download_source?.url);
    if (url && url.startsWith("https://")) return { kind: "available", url, source: missing.download_source };
    if (missing?.lookup_state === "pending") return { kind: "searching" };
    if (missing?.lookup_state === "not_found") return { kind: "not_found" };
    return { kind: "idle" };
}

export class DownloadController {
    constructor(dialog) {
        this.dialog = dialog;
        this.container = null;
        this.missing = new Map();   // refSlot -> missing model
        this.byRef = new Map();     // refSlot -> download id
        this.records = {};          // download id -> progress record
        this.progressTimer = null;
        this.lookupTimer = null;
    }

    blockId(missing) {
        return `dl-${refSlot(missing)}`;
    }

    renderBlock(missing) {
        // Node and subgraph ids inside the slot come from the workflow file
        return `<div id="${escapeHtml(this.blockId(missing))}" style="margin: 4px 0 8px;">${this.blockHtml(missing)}</div>`;
    }

    blockHtml(missing) {
        const slot = refSlot(missing);
        const record = this.records[this.byRef.get(slot)];
        const state = downloadState(missing, record);
        // dataset.mlSlot hands back the unescaped slot, which is the map key
        const button = (action, text) =>
            `<button data-ml-action="${action}" data-ml-slot="${escapeHtml(slot)}" class="model-linker-resolve-btn" style="padding:2px 8px;">${text}</button>`;
        switch (state.kind) {
            case "active": {
                const label = record.status === "queued" ? "Queued"
                    : record.status === "verifying" ? "Verifying"
                    : `${formatBytes(record.downloaded) || "0 B"} / ${formatBytes(record.total_size) || "?"}`;
                const speed = record.speed ? ` · ${formatBytes(record.speed)}/s` : "";
                return `<div style="display:flex; gap:8px; align-items:center;">☁ ${escapeHtml(label)}`
                    + `<progress max="100" value="${Number(record.progress) || 0}" style="flex:1;"></progress>`
                    + `<span style="opacity:.7; font-size:12px;">${escapeHtml(speed)}</span>${button("cancel", "Cancel")}</div>`;
            }
            case "done":
                return `<div style="color:#0aa96e;">✓ Downloaded${record.metadata === "written" ? " · Lora Manager info written" : ""}</div>`;
            case "error":
                return `<div style="color:#e05050;">✗ ${escapeHtml(state.message || "Download failed")}</div>`
                    + (state.auth ? button("settings", "⚙ Settings") + " " : "") + button("download", "Retry");
            case "available": {
                const source = state.source || {};
                const where = SOURCE_LABELS[source.url_source] || SOURCE_LABELS[source.source] || source.source || "online";
                const size = typeof source.size === "number" ? formatBytes(source.size)
                    : (typeof source.size === "string" ? source.size : "");
                return `<div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">`
                    + button("download", `☁ Download${size ? ` (${escapeHtml(size)})` : ""}`)
                    + `<span style="opacity:.7; font-size:12px;">from ${escapeHtml(where)}</span>`
                    + `<a href="${escapeHtml(state.url)}" target="_blank" rel="noopener noreferrer" title="Open the source">↗</a></div>`;
            }
            case "searching":
                return `<div style="opacity:.7;">🔍 Looking for a download…</div>`;
            case "not_found":
                return `<div style="opacity:.7;">No download found online. ${button("search", "Search again")}</div>`;
            case "idle":
                return `<div>${button("search", "🔍 Find a download")}</div>`;
            default:
                return "";
        }
    }

    refresh(missing) {
        const element = this.container?.querySelector(`#${CSS.escape(this.blockId(missing))}`);
        if (element) element.innerHTML = this.blockHtml(missing);
    }

    afterRender(container, data) {
        this.container = container;
        this.missing = new Map((data.missing_models || []).map((m) => [refSlot(m), m]));
        if (!container._mlDownloadsWired) {
            container.addEventListener("click", (event) => this.onClick(event));
            container._mlDownloadsWired = true;
        }
        this.reconnect();
        if ((data.lookups_pending || 0) > 0) this.scheduleLookupPoll();
    }

    onClick(event) {
        const button = event.target.closest?.("[data-ml-action]");
        if (!button || !this.container?.contains(button)) return;
        const action = button.dataset.mlAction;
        if (action === "settings") { this.dialog.openSettings(); return; }
        const missing = this.missing.get(button.dataset.mlSlot);
        if (!missing) return;
        if (action === "download") this.start(missing);
        else if (action === "cancel") this.cancel(missing);
        else if (action === "search") this.searchAgain(missing);
    }

    async request(path, body) {
        const response = await api.fetchApi(path, body === undefined ? {} : {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data?.error || `HTTP ${response.status}`);
        return data;
    }

    expectedSha256(missing) {
        const source = missing.download_source || {};
        if (typeof source.sha256 === "string" && /^[0-9a-f]{64}$/i.test(source.sha256)) return source.sha256;
        const type = (missing.source_hash_type || "sha256").toLowerCase();
        return type === "sha256" && /^[0-9a-f]{64}$/i.test(missing.source_hash || "") ? missing.source_hash : null;
    }

    async start(missing) {
        const source = missing.download_source || {};
        const category = missing.category && missing.category !== "unknown" ? missing.category : source.directory;
        try {
            const data = await this.request("/model_linker/download", {
                url: source.url,
                category,
                filename: basename(missing.original_path),
                sha256: this.expectedSha256(missing),
                size: typeof source.size === "number" ? Math.round(source.size) : null,
            });
            // Every card referencing the same file follows this one download
            for (const other of this.missing.values()) {
                if (fileKey(other) === fileKey(missing)) this.track(other, data.download_id);
            }
            this.scheduleProgressPoll();
        } catch (error) {
            notifyError("Download did not start", error);
        }
    }

    track(missing, downloadId, record = { status: "queued", progress: 0 }) {
        this.byRef.set(refSlot(missing), downloadId);
        this.records[downloadId] = this.records[downloadId] || record;
        this.refresh(missing);
    }

    async cancel(missing) {
        const id = this.byRef.get(refSlot(missing));
        if (!id) return;
        try {
            await this.request(`/model_linker/cancel/${encodeURIComponent(id)}`, {});
        } catch (error) {
            notifyError("Could not cancel the download", error);
        }
    }

    scheduleProgressPoll() {
        if (!this.progressTimer) this.progressTimer = setTimeout(() => this.pollProgress(), 1000);
    }

    async pollProgress() {
        this.progressTimer = null;
        let all;
        try {
            all = await this.request("/model_linker/progress");
        } catch (error) {
            this.scheduleProgressPoll();
            return;
        }
        let active = false;
        const finished = [];
        for (const [slot, id] of this.byRef) {
            const record = all[id];
            if (!record) continue;
            const before = this.records[id];
            this.records[id] = record;
            const missing = this.missing.get(slot);
            if (missing) this.refresh(missing);
            if (downloadState(missing, record).kind === "active") active = true;
            if (isFinished(record) && !isFinished(before) && !finished.includes(record)) finished.push(record);
        }
        if (active) this.scheduleProgressPoll();
        if (finished.length) await this.onFinished(finished);
    }

    async onFinished(records) {
        const names = [...new Set(records.map((r) => r.filename))].join(", ");
        app.extensionManager?.toast?.add({ severity: "success", summary: "Model Linker", detail: `Downloaded ${names}`, life: 5000 });
        try { await app.refreshComboInNodes?.(); } catch (e) { /* older frontends */ }
        await this.dialog.loadWorkflowData();
    }

    async reconnect() {
        let all;
        try {
            all = await this.request("/model_linker/progress");
        } catch (error) {
            return;
        }
        let any = false;
        for (const [id, record] of Object.entries(all)) {
            if (downloadState(null, record).kind !== "active") continue;
            for (const missing of this.missing.values()) {
                if (basename(missing.original_path).toLowerCase() === (record.filename || "").toLowerCase()) {
                    this.records[id] = record;
                    this.track(missing, id, record);
                    any = true;
                }
            }
        }
        if (any) this.scheduleProgressPoll();
    }

    scheduleLookupPoll() {
        if (!this.lookupTimer) this.lookupTimer = setTimeout(() => this.pollLookups(), 2000);
    }

    async pollLookups() {
        this.lookupTimer = null;
        const waiting = [...this.missing.values()].filter((m) => m.lookup_state === "pending");
        if (!waiting.length) return;
        let data;
        try {
            data = await this.request("/model_linker/lookups", { keys: [...new Set(waiting.map((m) => m.lookup_key))] });
        } catch (error) {
            this.scheduleLookupPoll();
            return;
        }
        for (const missing of waiting) {
            const result = data.results?.[missing.lookup_key];
            if (!result || result.state === "pending") continue;
            missing.lookup_state = result.state === "found" ? "found" : "not_found";
            missing.download_source = result.source || null;
            this.refresh(missing);
        }
        if ([...this.missing.values()].some((m) => m.lookup_state === "pending")) this.scheduleLookupPoll();
    }

    async searchAgain(missing) {
        try {
            const { original_path, category, source_url, source_hash, source_hash_type } = missing;
            const result = await this.request("/model_linker/lookups/refresh", {
                missing: { original_path, category, source_url, source_hash, source_hash_type },
            });
            for (const other of this.missing.values()) {
                if (other.lookup_key === missing.lookup_key) {
                    other.lookup_key = result.key;
                    other.lookup_state = "pending";
                    this.refresh(other);
                }
            }
            this.scheduleLookupPoll();
        } catch (error) {
            notifyError("Could not start the search", error);
        }
    }

    async downloadAll() {
        const seen = new Set();
        let started = 0;
        for (const missing of this.missing.values()) {
            const record = this.records[this.byRef.get(refSlot(missing))];
            if (downloadState(missing, record).kind !== "available") continue;
            if (seen.has(fileKey(missing))) continue;
            seen.add(fileKey(missing));
            await this.start(missing);
            started++;
        }
        if (!started) {
            app.extensionManager?.toast?.add({ severity: "info", summary: "Model Linker",
                detail: "Nothing to download yet - lookups may still be running.", life: 4000 });
        }
    }

    stop() {
        clearTimeout(this.progressTimer);
        clearTimeout(this.lookupTimer);
        this.progressTimer = this.lookupTimer = null;
    }
}
```

- [ ] **Step 4: Wire it into the dialog**

In `web/modules/linker-dialog.js`:

1. Import: `import { DownloadController } from "./downloads.js";`
2. In the constructor (before any rendering): `this.downloads = new DownloadController(this);`
3. Add a method next to `openOverridesManager`:

```js
    openSettings() {
        if (!this.settingsDialog) this.settingsDialog = new SettingsDialog();
        this.settingsDialog.show();
    }
```

   and change Plan A's settings header button `onclick` to `() => this.openSettings()`.
4. In `close()`, after aborting the analysis: `this.downloads.stop();` (downloads continue on the server).
5. In `renderMissingModel`, directly after the "Original source" block (the `if (sourceUrl) { ... }` ending around line 1319), add:

```js
        // Background lookups and downloads (web/modules/downloads.js)
        html += this.downloads.renderBlock(missing);
```

6. At the end of `displayMissingModels(container, data)` (after the per-model wiring loop that ends around line 1261), add:

```js
        this.downloads.afterRender(container, data);
```

7. In `createFooter`, create the button beside `autoBtn` and append it before `autoBtn`:

```js
        const downloadAllBtn = $el("button", {
            textContent: "☁ Download all found",
            title: "Download every missing model a source was found for (two at a time)",
            onclick: () => this.downloads.downloadAll(),
            className: "model-linker-resolve-btn",
        });
        footer.appendChild(downloadAllBtn);
```

- [ ] **Step 5: Run the whole suite**

Run: `python tests/run.py`
Expected: all pass, including `downloads`.

- [ ] **Step 6: Verify in the app**

Hard-refresh the browser. Load a workflow referencing `flux1-dev-fp8.safetensors` and one invented name (`nothing_like_this_123.safetensors`), each on two nodes.
1. Open the dialog: both cards show "🔍 Looking for a download…", then — without the list redrawing — the first shows "☁ Download (… GB) from HuggingFace (Comfy-Org / Kijai)", the second "No download found online".
2. Queue a manual selection on some card before the lookups finish; confirm it is still queued after they finish.
3. Close the dialog, reopen it: the results appear immediately (`lookup_state` from the store).
4. Start the download, close the dialog, reopen: both cards for that model show the running progress. Cancel works and leaves no `.part`.
5. "☁ Download all found" twice in a row starts one download per file and toasts nothing extra on the second click except server refusals.
Delete any downloaded files afterwards.

- [ ] **Step 7: Commit**

```bash
git add web/modules/downloads.js web/modules/linker-dialog.js tests/js/test_downloads.mjs
git commit -m "Show background lookup results and run downloads from the dialog"
```

---

### Task 8: Search for similar models online — scored, user-chosen, saved under their own name

**Files:**
- Create: `core/online_search.py`
- Modify: `core/routes_downloads.py` (search route), `web/modules/downloads.js` (similar-results list)
- Test: `tests/test_online_search.py` (new), `tests/js/test_downloads.mjs` (extend)

**Interfaces:**
- Consumes: `matcher.calculate_filename_confidence`, `matcher.STRONG_MATCH_CONFIDENCE`, `sources.huggingface.build_search_queries`, `sources.huggingface.get_huggingface_download_url`, `sources.huggingface.HF_API_URL`, `sources.civitai.CIVITAI_API_URL`, `sources.hf_index._load()`, `sources.hf_index._to_result(entry, match_type)`, `sources.model_list.search_model_list_multiple(filename, limit)`, `settings.auth_headers_for_url`, `categories.canonical_category`, `scanner.MODEL_EXTENSIONS`; frontend `DownloadController.track()`, `request()`, `refresh()`, `SOURCE_LABELS`, `formatBytes`.
- Produces:
  - `online_search.search_candidates(filename: str, category: str = '', limit: int = 15) -> {'candidates': [candidate], 'errors': [str]}` where a candidate is `{source, filename, url, page_url?, repo_id?, name?, base_model?, size?, sha256?, confidence: float, different_model: bool}`, sorted by confidence, deduplicated by URL.
  - Route `POST /model_linker/search` `{missing: {original_path, category}}` → that dict.

Why it is built this way rather than ported (agreed with the user on 2026-10-15):
- Upstream's partial search returns the **first** file whose name contains the other as a substring — `qwen3vl_4b` as readily as `qwen3vl_8b`. Here every candidate file from every source is scored with this fork's banded matcher, so a different size or generation lands below 70% and is labelled **"different model"**.
- A candidate is saved under **its own filename**, never the workflow's. A near match stored under the expected name would load silently as if it were the right model. Once downloaded it simply appears as a fuzzy match on the card, which the user reviews and selects like any other.
- It runs only when the user presses **🔎 Search similar**, off the event loop. Nothing found this way is put in `download_source`, so neither the background lookups nor "Download all found" can ever download a partial match.

- [ ] **Step 1: Write the failing Python tests**

Create `tests/test_online_search.py`:

```python
"""
Searching for similar models online: every candidate scored with the banded
matcher, different models labelled, sources failing independently.
"""

import unittest
from unittest import mock

try:
    import requests
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import online_search, settings

HF_SHA = 'ab' * 32


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def fake_get(routes, sent):
    def get(url, params=None, headers=None, timeout=None):
        sent.append((url, dict(headers or {})))
        for prefix, response in routes.items():
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return response
        return FakeResponse({}, 404)
    return get


# Longest prefix first: the tree URL also starts with the search URL
HF_ROUTES = {
    'https://huggingface.co/api/models/org/qwen/tree/main': FakeResponse([
        {'type': 'file', 'path': 'split/qwen3vl_8b_bf16.safetensors', 'size': 10, 'lfs': {'size': 16e9, 'oid': HF_SHA}},
        {'type': 'file', 'path': 'split/qwen3vl_4b_int8.safetensors', 'lfs': {'size': 4e9, 'oid': 'cd' * 32}},
        {'type': 'file', 'path': 'README.md', 'size': 1},
        {'type': 'directory', 'path': 'split'},
    ]),
    'https://huggingface.co/api/models': FakeResponse([{'id': 'org/qwen'}]),
}
CIVITAI_ROUTE = {
    'https://civitai.com/api/v1/models': FakeResponse({'items': [
        {'id': 7, 'name': 'Qwen VL tune', 'type': 'Checkpoint', 'modelVersions': [
            {'id': 70, 'baseModel': 'Qwen', 'files': [
                {'name': 'qwen3vl_8b_fp8.safetensors', 'downloadUrl': 'https://civitai.com/api/download/models/70',
                 'sizeKB': 8e6, 'hashes': {'SHA256': 'EF' * 32}}]}]},
        {'id': 8, 'name': 'A style', 'type': 'LORA', 'modelVersions': [
            {'id': 80, 'files': [{'name': 'qwen3vl_style.safetensors',
                                  'downloadUrl': 'https://civitai.com/api/download/models/80', 'sizeKB': 100}]}]},
    ]}),
}


class SearchTests(unittest.TestCase):
    def search(self, routes, category='checkpoints'):
        self.sent = []
        with mock.patch.object(requests, 'get', fake_get(routes, self.sent)), \
             mock.patch.object(online_search, '_index_candidates', return_value=[]), \
             mock.patch.object(online_search, '_model_list_candidates', return_value=[]):
            return online_search.search_candidates('qwen3vl_8b_int8.safetensors', category)

    def test_same_model_ranks_above_other_size(self):
        candidates = self.search({**HF_ROUTES, **CIVITAI_ROUTE})['candidates']
        names = [c['filename'] for c in candidates]
        self.assertLess(names.index('qwen3vl_8b_bf16.safetensors'), names.index('qwen3vl_4b_int8.safetensors'))
        by_name = {c['filename']: c for c in candidates}
        self.assertTrue(by_name['qwen3vl_4b_int8.safetensors']['different_model'])
        self.assertFalse(by_name['qwen3vl_8b_bf16.safetensors']['different_model'])

    def test_only_model_files_and_the_right_civitai_type(self):
        names = [c['filename'] for c in self.search({**HF_ROUTES, **CIVITAI_ROUTE})['candidates']]
        self.assertNotIn('README.md', names)
        self.assertNotIn('qwen3vl_style.safetensors', names)  # a LoRA, but a checkpoint is missing
        self.assertIn('qwen3vl_8b_fp8.safetensors', names)

    def test_hashes_come_along_for_verification(self):
        by_name = {c['filename']: c for c in self.search({**HF_ROUTES, **CIVITAI_ROUTE})['candidates']}
        self.assertEqual(by_name['qwen3vl_8b_bf16.safetensors']['sha256'], HF_SHA)
        self.assertEqual(by_name['qwen3vl_8b_fp8.safetensors']['sha256'], 'ef' * 32)
        self.assertEqual(by_name['qwen3vl_8b_bf16.safetensors']['url'],
                         'https://huggingface.co/org/qwen/resolve/main/split/qwen3vl_8b_bf16.safetensors')

    def test_a_failing_source_does_not_hide_the_others(self):
        routes = {**HF_ROUTES, 'https://civitai.com/api/v1/models': requests.ConnectionError('down')}
        result = self.search(routes)
        self.assertTrue(any(c['source'] == 'huggingface' for c in result['candidates']))
        self.assertTrue(any('CivitAI' in e for e in result['errors']))

    def test_tokens_go_only_to_their_own_host(self):
        with mock.patch.object(settings, 'get_token', side_effect=lambda p: f'{p}-token'):
            self.search({**HF_ROUTES, **CIVITAI_ROUTE})
        for url, headers in self.sent:
            auth = headers.get('Authorization', '')
            if url.startswith('https://huggingface.co/'):
                self.assertEqual(auth, 'Bearer huggingface-token')
            elif url.startswith('https://civitai.com/'):
                self.assertEqual(auth, 'Bearer civitai-token')


class LocalCandidateTests(unittest.TestCase):
    def test_bundled_index_is_searched_without_network(self):
        with mock.patch.object(requests, 'get', side_effect=AssertionError('no network here')):
            found = online_search._index_candidates('flux1-dev-fp8-e4m3fn.safetensors')
        self.assertTrue(any(c['filename'] == 'flux1-dev-fp8.safetensors' for c in found))
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m unittest discover -t . -s tests -p "test_online_search.py" -v`
Expected: ERROR — `cannot import name 'online_search' from 'core'`.

- [ ] **Step 3: Implement `core/online_search.py`**

```python
"""
Search for models *similar* to a missing one, when no exact or hash match exists.

Every candidate file from every source is scored with the banded matcher, so a
different size or generation of the model is labelled rather than offered as a
near miss. Results are only ever shown for the user to choose from: they are
never stored as a lookup's download source, and a chosen file is saved under
its own name (see downloads.js), so a near match can never pass for the exact
file the workflow names.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from . import settings
from .categories import canonical_category
from .matcher import STRONG_MATCH_CONFIDENCE, calculate_filename_confidence
from .scanner import MODEL_EXTENSIONS
from .sources import hf_index
from .sources.civitai import CIVITAI_API_URL
from .sources.huggingface import HF_API_URL, build_search_queries, get_huggingface_download_url
from .sources.model_list import search_model_list_multiple

logger = logging.getLogger(__name__)

MAX_REPOS = 5
TIMEOUT = 15

# CivitAI model types worth offering for a node's folder; unknown folders take any
_CIVITAI_TYPES = {
    'loras': {'LORA', 'LOCON', 'DORA', 'LYCORIS'},
    'checkpoints': {'CHECKPOINT'},
    'diffusion_models': {'CHECKPOINT'},
    'embeddings': {'TEXTUALINVERSION'},
    'vae': {'VAE'},
    'controlnet': {'CONTROLNET'},
    'upscale_models': {'UPSCALER'},
}


def _is_model_file(name: str) -> bool:
    return os.path.splitext(name or '')[1].lower() in MODEL_EXTENSIONS


def _get(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    import requests
    response = requests.get(url, params=params, headers=settings.auth_headers_for_url(url), timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f'HTTP {response.status_code}')
    return response.json()


def _huggingface_candidates(filename: str) -> List[Dict[str, Any]]:
    repos: List[str] = []
    for query in build_search_queries(filename):
        for model in _get(f'{HF_API_URL}/models', {'search': query, 'limit': MAX_REPOS}) or []:
            repo = model.get('id') or model.get('modelId')
            if repo and repo not in repos:
                repos.append(repo)
        if len(repos) >= MAX_REPOS:
            break
    found = []
    for repo in repos[:MAX_REPOS]:
        try:
            files = _get(f'{HF_API_URL}/models/{repo}/tree/main', {'recursive': 'true'}) or []
        except Exception as e:
            logger.debug(f"Model Linker: could not list {repo}: {e}")
            continue
        for entry in files:
            path = entry.get('path', '')
            if entry.get('type') != 'file' or not _is_model_file(path):
                continue
            lfs = entry.get('lfs') or {}
            found.append({
                'source': 'huggingface', 'repo_id': repo, 'filename': os.path.basename(path), 'path': path,
                'url': get_huggingface_download_url(repo, path),
                'page_url': f'https://huggingface.co/{repo}',
                'size': lfs.get('size') or entry.get('size'),
                'sha256': (lfs.get('oid') or '').lower(),
            })
    return found


def _civitai_candidates(filename: str, category: str) -> List[Dict[str, Any]]:
    query = build_search_queries(filename)[-1]
    allowed = _CIVITAI_TYPES.get(canonical_category(category) or '')
    found = []
    for item in (_get(f'{CIVITAI_API_URL}/models', {'query': query, 'limit': 10}) or {}).get('items', []):
        if allowed and str(item.get('type', '')).upper() not in allowed:
            continue
        for version in item.get('modelVersions') or []:
            for entry in version.get('files') or []:
                name, url = entry.get('name', ''), entry.get('downloadUrl', '')
                if not url or not _is_model_file(name):
                    continue
                found.append({
                    'source': 'civitai', 'filename': name, 'url': url,
                    'page_url': f"https://civitai.com/models/{item.get('id')}?modelVersionId={version.get('id')}",
                    'name': item.get('name', ''), 'base_model': version.get('baseModel', ''),
                    'size': (entry.get('sizeKB') or 0) * 1024 or None,
                    'sha256': str((entry.get('hashes') or {}).get('SHA256', '')).lower(),
                })
    return found


def _index_candidates(filename: str) -> List[Dict[str, Any]]:
    """Every file in the bundled Comfy-Org/Kijai index - local, no network."""
    seen, found = set(), []
    for entries in hf_index._load()['by_filename'].values():
        for entry in entries:
            key = (entry.get('repo_id'), entry.get('path'))
            if key in seen:
                continue
            seen.add(key)
            found.append(hf_index._to_result(entry, 'similar'))
    return found


def _model_list_candidates(filename: str) -> List[Dict[str, Any]]:
    return search_model_list_multiple(filename, limit=10)


def search_candidates(filename: str, category: str = '', limit: int = 15) -> Dict[str, Any]:
    filename = os.path.basename((filename or '').replace('\\', '/'))
    sources = (
        ('the bundled HuggingFace index', lambda: _index_candidates(filename)),
        ("ComfyUI-Manager's list", lambda: _model_list_candidates(filename)),
        ('HuggingFace', lambda: _huggingface_candidates(filename)),
        ('CivitAI', lambda: _civitai_candidates(filename, category)),
    )
    candidates, errors = [], []
    for label, collect in sources:
        try:
            candidates.extend(collect())
        except Exception as e:
            errors.append(f'{label}: {e}')

    best: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        url = candidate.get('url') or ''
        if not url.startswith('https://') or not candidate.get('filename'):
            continue
        confidence = calculate_filename_confidence(filename, candidate['filename'])
        candidate = {**candidate, 'confidence': confidence,
                     'different_model': confidence < STRONG_MATCH_CONFIDENCE}
        if url not in best or confidence > best[url]['confidence']:
            best[url] = candidate

    ranked = sorted(best.values(), key=lambda c: c['confidence'], reverse=True)
    return {'candidates': ranked[:limit], 'errors': errors}
```

- [ ] **Step 4: Run the Python tests**

Run: `python -m unittest discover -t . -s tests -p "test_online_search.py" -v`
Expected: PASS. Two things to check if not:
- `fake_get` matches routes in dict order, and the tree URL also starts with the search URL — keep the tree route first in `HF_ROUTES`.
- If `test_bundled_index_is_searched_without_network` fails because `flux1-dev-fp8.safetensors` is not in the bundled index, choose any filename `hf_index.lookup_by_filename` does find and search for a variant spelling of it.

- [ ] **Step 5: Add the route**

In `core/routes_downloads.py`, add `from . import online_search` to the imports and inside `register`:

```python
    @routes.post("/model_linker/search")
    async def search_similar(request):
        try:
            data = await request.json()
        except Exception:
            return _bad(web, 'Body must be JSON')
        missing = data.get('missing') if isinstance(data, dict) else None
        if not isinstance(missing, dict) or not isinstance(missing.get('original_path'), str):
            return _bad(web, 'missing.original_path is required')
        category = missing.get('category') if isinstance(missing.get('category'), str) else ''
        result = await asyncio.to_thread(online_search.search_candidates, missing['original_path'], category)
        return web.json_response(result)
```

- [ ] **Step 6: Extend the frontend test**

In `tests/js/test_downloads.mjs`, before `return check.failures;` add:

```js
  const similar = src.slice(src.indexOf('similarHtml(missing) {'), src.indexOf('async downloadAll'));
  check('similar results are escaped', /escapeHtml\(candidate\.filename\)/.test(similar));
  check('different models are labelled', /different model/.test(similar));
  check('a similar file keeps its own name',
        /filename:\s*candidate\.filename/.test(src.slice(src.indexOf('async startSimilar'))));
  check('download all never takes a similar result',
        !/similar/i.test(src.slice(src.indexOf('async downloadAll'), src.indexOf('stop() {'))));
  check('searching similar is offered when no exact download exists',
        /"similar"/.test(src.slice(src.indexOf('blockHtml(missing) {'), src.indexOf('refresh(missing) {'))));
```

- [ ] **Step 7: Run to verify it fails**

Run: `node tests/js/run.mjs` — expected: FAIL on the five new checks.

- [ ] **Step 8: Implement the similar-results list**

In `web/modules/downloads.js`:

1. Restructure `blockHtml(missing)` so each `case` assigns `html = …; break;` instead of returning (declare `let html = "";` before the `switch`), add `button("similar", "🔎 Search similar")` to the `not_found` and `idle` cases' markup, and end the method with:

```js
        if (["available", "error", "not_found", "idle", "searching"].includes(state.kind)) {
            html += this.similarHtml(missing);
        }
        return html;
```

2. Add these methods to `DownloadController`, directly before `async downloadAll()`:

```js
    similarHtml(missing) {
        const slot = refSlot(missing);
        const similar = missing.similar;
        if (!similar) return "";
        if (similar.loading) return `<div style="opacity:.7; margin-top:4px;">🔎 Searching HuggingFace and CivitAI…</div>`;
        const errors = (similar.errors || []).map((e) => `<div style="opacity:.6; font-size:11px;">⚠ ${escapeHtml(e)}</div>`).join("");
        if (!similar.candidates?.length) return `<div style="opacity:.7; margin-top:4px;">No similar models found.</div>${errors}`;
        const rows = similar.candidates.map((candidate, index) => {
            const page = safeHttpUrl(candidate.page_url);
            const size = typeof candidate.size === "number" ? formatBytes(candidate.size) : (candidate.size || "");
            const tone = candidate.different_model ? "#e05050" : "#e0a030";
            const label = candidate.different_model ? "different model" : "similar";
            const where = SOURCE_LABELS[candidate.url_source] || SOURCE_LABELS[candidate.source] || candidate.source || "";
            return `<li style="margin:2px 0; display:flex; gap:6px; align-items:center; flex-wrap:wrap;">`
                + `<code>${escapeHtml(candidate.filename)}</code>`
                + `<span style="color:${tone}; font-size:12px;">${escapeHtml(String(candidate.confidence))}% · ${label}</span>`
                + `<span style="opacity:.6; font-size:12px;">${escapeHtml(where)}${size ? ` · ${escapeHtml(size)}` : ""}</span>`
                + (page ? `<a href="${escapeHtml(page)}" target="_blank" rel="noopener noreferrer">↗</a>` : "")
                + `<button data-ml-action="download-similar" data-ml-slot="${escapeHtml(slot)}" data-ml-index="${index}" class="model-linker-resolve-btn" style="padding:1px 6px;">☁ Download</button>`
                + `</li>`;
        }).join("");
        return `<div style="margin-top:6px; font-size:12px; opacity:.8;">Similar models online — saved under their own name; link one afterwards if it is right:</div>`
            + `<ul style="margin:4px 0; padding-left:16px;">${rows}</ul>${errors}`;
    }

    async searchSimilar(missing) {
        missing.similar = { loading: true };
        this.refresh(missing);
        try {
            const { original_path, category } = missing;
            missing.similar = await this.request("/model_linker/search", { missing: { original_path, category } });
        } catch (error) {
            missing.similar = { candidates: [], errors: [error.message] };
        }
        this.refresh(missing);
    }

    async startSimilar(missing, index) {
        const candidate = missing.similar?.candidates?.[index];
        if (!candidate) return;
        if (!missing.category || missing.category === "unknown") {
            notifyError("Download did not start", new Error("The node's model folder is unknown"));
            return;
        }
        try {
            const data = await this.request("/model_linker/download", {
                url: candidate.url,
                category: missing.category,
                // Its own name - never the workflow's, which would let a near
                // match pass silently for the exact file
                filename: candidate.filename,
                sha256: /^[0-9a-f]{64}$/i.test(candidate.sha256 || "") ? candidate.sha256 : null,
                size: typeof candidate.size === "number" ? Math.round(candidate.size) : null,
            });
            this.track(missing, data.download_id);
            this.scheduleProgressPoll();
        } catch (error) {
            notifyError("Download did not start", error);
        }
    }
```

3. In `onClick`, extend the action dispatch:

```js
        else if (action === "similar") this.searchSimilar(missing);
        else if (action === "download-similar") this.startSimilar(missing, Number(button.dataset.mlIndex));
```

`downloadAll` stays as it is: it only takes `downloadState(...).kind === "available"`, which comes from `download_source`, which only the exact/hash chain ever sets.

- [ ] **Step 9: Run the whole suite**

Run: `python tests/run.py` — expected: all pass.

- [ ] **Step 10: Verify in the app**

Restart ComfyUI. Load a workflow referencing `qwen3vl_8b_int8.safetensors` (or any name with no exact match anywhere). Its card says "No download found online". Press 🔎 Search similar: within a few seconds a list appears — same-size variants first in orange "similar", other sizes in red "different model". Download one: it lands under its own name, and after the automatic re-analysis it is listed as a fuzzy match on the card, ready to select. "☁ Download all found" does not touch it. Delete the file afterwards.

- [ ] **Step 11: Commit**

```bash
git add core/online_search.py core/routes_downloads.py web/modules/downloads.js tests/test_online_search.py tests/js/test_downloads.mjs
git commit -m "Search for similar models online, scored and saved under their own name"
```

---

### Task 9: Fill empty Lora Manager info from HuggingFace

**Files:**
- Create: `core/lm_api.py`, `core/hf_identify.py`, `core/lm_backfill.py`
- Modify: `core/lm_sidecar.py` (use `lm_api`; hydrate HuggingFace downloads), `core/routes_downloads.py` (backfill routes), `web/modules/settings-dialog.js` (Lora Manager section)
- Test: `tests/test_hf_identify.py`, `tests/test_lm_backfill.py` (new), `tests/js/test_settings_dialog.mjs` (extend)

**Interfaces:**
- Consumes: `lm_sidecar.lm_target`, `lm_sidecar.sidecar_path`, `downloader.sha256_file`, `linker.group_models_by_physical_file`, `scanner.get_model_files`, `sources.hf_index.lookup_by_sha256`, `sources.huggingface.build_search_queries`, `settings.auth_headers_for_url`, `settings.provider_for_url`, `overrides._data_dir`.
- Produces:
  - `lm_api.scan(prefix, root=None)` (scoped to `root` when Lora Manager knows it, else a full scan), `link_huggingface(model_path, repo)`, `hydrate_huggingface(model_path, model_root, repo)` (a model outside `model_root`, including on another drive, raises `LoraManagerUnavailable`), `save_metadata(prefix, model_path, fields)`, `base_models() -> list[str]` (read from Lora Manager's `{"success", "data": {"models": [...]}}`), `enrich_with_ai(model_paths)`, `available() -> bool`; all raise `lm_api.LoraManagerUnavailable` on failure.
  - `hf_identify.repo_from_url(url) -> Optional[str]`, `hf_identify.card(repo) -> Optional[dict]`, `hf_identify.find_repo(filename, sha256) -> Optional[{'repo', 'card', 'how'}]`, `hf_identify.base_model_label(card, allowed) -> Optional[str]`, `hf_identify.card_tags(card) -> list[str]`, `hf_identify.clear_cache()`.
  - `lm_backfill.start(use_ai=False, limit=None) -> bool` (`limit` = fill at most that many models: the sample run), `lm_backfill.cancel()`, `lm_backfill.status() -> dict` (`status, total, candidates_total, limit, done, current, counts, results, use_ai, ai_error, error, started_at, finished_at`).
  - Routes: `POST /model_linker/lm-backfill/start` `{use_ai: bool, limit?: int > 0}` → `{started, state}`; `GET /model_linker/lm-backfill` → state; `POST /model_linker/lm-backfill/cancel`.
  - Models whose sidecar has `exclude` or `skip_metadata_refresh` set are never touched — the user opted them out in Lora Manager.

**What the research found (Lora Manager v1.2.5, read-only; GPL-3.0, so nothing is copied):**
- Lora Manager already turns a HuggingFace model card into sidecar data: the README as HTML in `modelDescription`, `widget:`/table/markdown/`<img>` images into `civitai.images`, `instance_prompt:` into `civitai.trainedWords`, a `<stem>.webp` preview, and `metadata_source: "source:huggingface"`, `source_platform: "huggingface"`, `source_url`/`hf_url: "https://huggingface.co/<owner>/<repo>"`, `from_civitai: false`. With its LLM configured (Ollama, on this install) its `enrich_hf_metadata` skill also sets `base_model`, `tags`, `notes` and `usage_tips`.
- What it **cannot** do is find which repo a file came from: hydration only runs for a sidecar already linked to a repo. That identification is this task's job; the README work is delegated to Lora Manager through its own routes, so the result is exactly its format, and its cache stays consistent (externally edited sidecars are not re-read by its cache).
- Routes used (`py/routes/handlers/model_source_handlers.py` and the agent/misc registrars): `POST /api/lm/set-hf-url` `{file_path, source_url}` links; `POST /api/lm/download-model-source` `{platform: "huggingface", repo, filename, model_root, relative_path}` hydrates when the file already exists and is linked; `POST /api/lm/{prefix}/save-metadata` `{file_path, ...fields}` merges fields (dicts shallow-merged, tags lowercased); `GET /api/lm/base-models` → `{"success": true, "data": {"models": [...], ...}}` (`py/routes/handlers/base_model_handlers.py`); `POST /api/lm/agent/execute/enrich_hf_metadata` `{model_paths}` (answers at once and runs in the background; 400 when no LLM is configured); `GET /api/lm/{prefix}/scan`, optionally `?roots=<root>` (400 for a root it does not know).
- Quirk handled here: hydration **clears `civitai.trainedWords`** when the README has no `instance_prompt`. Existing trigger words are saved before and merged back after.
- Never write a fake `civitai.id`/`modelId`: it turns on Lora Manager's "View on Civitai" link.
- Measured on this machine (2026-10-15): "empty" sidecars (no `civitai.id`, empty `modelDescription`, no `source_url`/`hf_url`) — loras 270, checkpoints/diffusion_models 115, text_encoders 126, vae 77, upscale_models 143; plus 82 model files with no sidecar. Many empty LoRAs (`ars\`, `gt\`) are the user's own trainings and will correctly come back "not found". Most vae/upscaler/text-encoder sidecars have `sha256: ""` with `hash_status: "pending"`, so those are hashed here.

**Identification — proven by hash, never guessed by name.** For each empty model: (1) a `source_url`/`hf_url` already on the sidecar is used as-is; (2) the SHA-256 is looked up in the bundled Comfy-Org/Kijai index; (3) up to three repos found by HuggingFace search on the filename are fetched with `GET /api/models/{repo}?blobs=true`, and a repo counts only if one of its files has an LFS `sha256` equal to the local file's. A name-only resemblance never links anything. Models still unidentified are reported; the user can link them by hand in Lora Manager ("Link model"), and the next run hydrates them through path (1).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hf_identify.py`:

```python
"""Identifying a local file's HuggingFace repo: by hash, never by name alone."""

import unittest
from unittest import mock

try:
    import requests
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import hf_identify

SHA = 'ab' * 32


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload


def card_payload(repo, sha, card_data=None):
    return {'id': repo, 'cardData': card_data or {}, 'tags': [],
            'siblings': [{'rfilename': 'm.safetensors', 'lfs': {'sha256': sha, 'size': 1}}]}


class RepoFromUrlTests(unittest.TestCase):
    def test_parses_repo_pages_and_file_links(self):
        for url in ('https://huggingface.co/owner/repo', 'https://huggingface.co/owner/repo/',
                    'https://huggingface.co/owner/repo/resolve/main/sub/x.safetensors',
                    'https://huggingface.co/owner/repo/blob/main/x.safetensors'):
            self.assertEqual(hf_identify.repo_from_url(url), 'owner/repo', url)

    def test_rejects_other_hosts_and_non_model_pages(self):
        for url in ('https://huggingface.co.evil.example/owner/repo', 'https://huggingface.co/datasets/a/b',
                    'https://huggingface.co/spaces/a/b', 'https://huggingface.co/owner', '', None):
            self.assertIsNone(hf_identify.repo_from_url(url), url)


class FindRepoTests(unittest.TestCase):
    def setUp(self):
        hf_identify._cards.clear()
        self.addCleanup(hf_identify._cards.clear)

    def routes(self, mapping):
        def get(url, params=None, headers=None, timeout=None):
            for prefix, response in mapping.items():
                if url.startswith(prefix):
                    return response
            return FakeResponse({}, 404)
        return mock.patch.object(requests, 'get', get)

    def test_hash_verified_search_result(self):
        with mock.patch.object(hf_identify.hf_index, 'lookup_by_sha256', return_value=None), self.routes({
            'https://huggingface.co/api/models/owner/right': FakeResponse(card_payload('owner/right', SHA)),
            'https://huggingface.co/api/models/owner/wrong': FakeResponse(card_payload('owner/wrong', 'cd' * 32)),
            'https://huggingface.co/api/models': FakeResponse([{'id': 'owner/wrong'}, {'id': 'owner/right'}]),
        }):
            found = hf_identify.find_repo('boogu_image_turbo_bf16.safetensors', SHA)
        self.assertEqual(found['repo'], 'owner/right')
        self.assertEqual(found['how'], 'hash-verified search')

    def test_same_name_without_the_hash_is_not_a_match(self):
        with mock.patch.object(hf_identify.hf_index, 'lookup_by_sha256', return_value=None), self.routes({
            'https://huggingface.co/api/models/owner/wrong': FakeResponse(card_payload('owner/wrong', 'cd' * 32)),
            'https://huggingface.co/api/models': FakeResponse([{'id': 'owner/wrong'}]),
        }):
            self.assertIsNone(hf_identify.find_repo('m.safetensors', SHA))

    def test_bundled_index_answers_first(self):
        hit = {'repo_id': 'Comfy-Org/x', 'path': 'a/m.safetensors'}
        with mock.patch.object(hf_identify.hf_index, 'lookup_by_sha256', return_value=hit), self.routes({
            'https://huggingface.co/api/models/Comfy-Org/x': FakeResponse(card_payload('Comfy-Org/x', SHA)),
        }):
            found = hf_identify.find_repo('m.safetensors', SHA)
        self.assertEqual((found['repo'], found['how']), ('Comfy-Org/x', 'bundled index'))


class CardTests(unittest.TestCase):
    def test_base_model_label_only_from_lora_managers_own_list(self):
        card = {'cardData': {'base_model': ['black-forest-labs/FLUX.1-dev']}}
        self.assertEqual(hf_identify.base_model_label(card, ['Flux.1 D', 'SDXL 1.0']), 'Flux.1 D')
        self.assertIsNone(hf_identify.base_model_label(card, ['SDXL 1.0']))
        self.assertIsNone(hf_identify.base_model_label({'cardData': {'base_model': 'someone/odd-model'}}, ['Flux.1 D']))

    def test_tags_drop_hub_bookkeeping(self):
        card = {'cardData': {'tags': ['LoRA', 'flux', 'base_model:x/y', 'license:mit', 'flux']}}
        self.assertEqual(hf_identify.card_tags(card), ['lora', 'flux'])
```

Create `tests/test_lm_backfill.py`:

```python
"""Filling empty Lora Manager info: which models, in which order, and what is kept."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

try:
    import requests  # noqa: F401
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import hf_identify, lm_api, lm_backfill, overrides
from support import LibraryTestCase

SHA = 'ab' * 32


def sidecar(**fields):
    base = {'file_name': 'm', 'model_name': 'm', 'file_path': '', 'size': 1, 'modified': 1.0,
            'sha256': SHA, 'base_model': 'Unknown', 'preview_url': '', 'civitai': {},
            'modelDescription': '', 'tags': [], 'hash_status': 'completed'}
    base.update(fields)
    return base


class BackfillTestCase(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.data_dir = tempfile.mkdtemp(prefix='model-linker-backfill-')
        self._real = overrides._data_dir
        overrides._data_dir = lambda: self.data_dir
        self.loras = self.add_models('loras', ['m.safetensors'], ['.safetensors'])
        self.model = os.path.join(self.loras, 'm.safetensors')
        self.calls = []
        fake = mock.MagicMock()
        fake.LoraManagerUnavailable = lm_api.LoraManagerUnavailable
        fake.available.return_value = True
        fake.base_models.return_value = ['Flux.1 D']
        for name in ('scan', 'link_huggingface', 'hydrate_huggingface', 'save_metadata', 'enrich_with_ai'):
            getattr(fake, name).side_effect = (lambda n: lambda *a, **k: self.calls.append((n, a, k)))(name)
        for patcher in (mock.patch.object(lm_backfill, 'lm_api', fake),
                        mock.patch.object(lm_backfill, 'PAUSE_BETWEEN_MODELS', 0)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        overrides._data_dir = self._real
        shutil.rmtree(self.data_dir, ignore_errors=True)
        super().tearDown()

    def write_sidecar(self, **fields):
        with open(os.path.join(self.loras, 'm.metadata.json'), 'w', encoding='utf-8') as handle:
            json.dump(sidecar(**fields), handle)

    def names(self):
        return [c[0] for c in self.calls]


class SelectionTests(BackfillTestCase):
    def test_only_empty_sidecars_are_candidates(self):
        self.write_sidecar()
        self.assertEqual(len(lm_backfill.candidates()), 1)
        self.write_sidecar(modelDescription='<p>has info</p>')
        self.assertEqual(lm_backfill.candidates(), [])
        self.write_sidecar(civitai={'id': 5})
        self.assertEqual(lm_backfill.candidates(), [])

    def test_linked_but_empty_is_a_candidate(self):
        self.write_sidecar(source_url='https://huggingface.co/o/r')
        self.assertEqual(len(lm_backfill.candidates()), 1)

    def test_models_opted_out_in_lora_manager_are_left_alone(self):
        for flag in ('exclude', 'skip_metadata_refresh'):
            self.write_sidecar(**{flag: True})
            self.assertEqual(lm_backfill.candidates(), [], flag)


class RunTests(BackfillTestCase):
    def test_linked_model_is_hydrated_without_searching(self):
        self.write_sidecar(source_url='https://huggingface.co/o/r')
        with mock.patch.object(hf_identify, 'find_repo', side_effect=AssertionError('no search')), \
             mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=False)
        self.assertEqual(self.names().count('hydrate_huggingface'), 1)
        self.assertNotIn('link_huggingface', self.names())
        self.assertEqual(lm_backfill.status()['counts']['hydrated'], 1)

    def test_identified_model_is_linked_hydrated_and_labelled(self):
        self.write_sidecar()
        card = {'cardData': {'base_model': 'black-forest-labs/FLUX.1-dev', 'tags': ['style']}}
        with mock.patch.object(hf_identify, 'find_repo',
                               return_value={'repo': 'o/r', 'card': card, 'how': 'hash-verified search'}):
            lm_backfill._run(use_ai=False)
        self.assertIn('scan', self.names())  # one per Lora Manager model type, before anything else
        self.assertEqual([n for n in self.names() if n != 'scan'][:2], ['link_huggingface', 'hydrate_huggingface'])
        saved = [c for c in self.calls if c[0] == 'save_metadata'][-1][1][2]
        self.assertEqual(saved['base_model'], 'Flux.1 D')
        self.assertEqual(saved['tags'], ['style'])

    def test_not_found_is_reported_and_nothing_written(self):
        self.write_sidecar()
        with mock.patch.object(hf_identify, 'find_repo', return_value=None):
            lm_backfill._run(use_ai=False)
        self.assertEqual(lm_backfill.status()['counts']['not_found'], 1)
        self.assertNotIn('link_huggingface', self.names())
        with open(os.path.join(self.data_dir, 'lm-backfill.json'), encoding='utf-8') as handle:
            self.assertEqual(json.load(handle)['results'][0]['outcome'], 'not_found')

    def test_trigger_words_survive_hydration(self):
        self.write_sidecar(source_url='https://huggingface.co/o/r', civitai={'trainedWords': ['STORYBOARD8']})

        def hydrate(*args, **kwargs):
            self.calls.append(('hydrate_huggingface', args, kwargs))
            self.write_sidecar(source_url='https://huggingface.co/o/r', modelDescription='<p>x</p>',
                               civitai={'trainedWords': ['readme_word']})

        lm_backfill.lm_api.hydrate_huggingface.side_effect = hydrate
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=False)
        saved = [c for c in self.calls if c[0] == 'save_metadata'][-1][1][2]
        self.assertEqual(saved['civitai']['trainedWords'], ['STORYBOARD8', 'readme_word'])

    def test_missing_hash_is_computed_and_recorded(self):
        self.write_sidecar(sha256='', hash_status='pending', source_url='https://huggingface.co/o/r')
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=False)
        first_save = next(c for c in self.calls if c[0] == 'save_metadata')[1][2]
        self.assertEqual(first_save['hash_status'], 'completed')
        self.assertEqual(len(first_save['sha256']), 64)

    def test_ai_enrichment_is_opt_in_and_batched(self):
        self.write_sidecar(source_url='https://huggingface.co/o/r')
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=False)
            self.assertNotIn('enrich_with_ai', self.names())
            self.write_sidecar(source_url='https://huggingface.co/o/r')
            lm_backfill._run(use_ai=True)
        self.assertEqual(self.names().count('enrich_with_ai'), 1)

    def test_ai_failure_is_reported_without_failing_the_run(self):
        self.write_sidecar(source_url='https://huggingface.co/o/r')
        lm_backfill.lm_api.enrich_with_ai.side_effect = lm_api.LoraManagerUnavailable(
            'LLM provider is not configured')
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=True)
        state = lm_backfill.status()
        self.assertEqual(state['status'], 'done')
        self.assertEqual(state['counts']['hydrated'], 1)
        self.assertIn('not configured', state['ai_error'])

    def test_limit_runs_a_sample(self):
        self.add_models('loras', ['n.safetensors'], ['.safetensors'])
        self.write_sidecar()
        with open(os.path.join(self.loras, 'n.metadata.json'), 'w', encoding='utf-8') as handle:
            json.dump(sidecar(file_name='n'), handle)
        with mock.patch.object(hf_identify, 'find_repo', return_value=None):
            lm_backfill._run(use_ai=False, limit=1)
        state = lm_backfill.status()
        self.assertEqual((state['total'], state['candidates_total'], state['done']), (1, 2, 1))

    def test_card_cache_lasts_one_run(self):
        hf_identify._cards['stale/repo'] = {'cardData': {}}
        self.write_sidecar()
        with mock.patch.object(hf_identify, 'find_repo', return_value=None):
            lm_backfill._run(use_ai=False)
        self.assertNotIn('stale/repo', hf_identify._cards)

    def test_lora_manager_not_running(self):
        lm_backfill.lm_api.available.return_value = False
        lm_backfill._run(use_ai=False)
        self.assertEqual(lm_backfill.status()['status'], 'error')


class LmApiTests(unittest.TestCase):
    def test_base_models_reads_lora_managers_envelope(self):
        answer = {'success': True, 'data': {'models': ['Flux.1 D', 'SDXL 1.0'], 'source': 'cache'}}
        with mock.patch.object(lm_api, '_call', return_value=answer):
            self.assertEqual(lm_api.base_models(), ['Flux.1 D', 'SDXL 1.0'])

    def test_hydrating_a_model_outside_its_root_is_refused(self):
        root = tempfile.mkdtemp(prefix='model-linker-root-')
        self.addCleanup(shutil.rmtree, root, True)
        outside = os.path.join(os.path.dirname(root), 'elsewhere', 'm.safetensors')
        with mock.patch.object(lm_api, '_call', side_effect=AssertionError('must not be called')):
            with self.assertRaises(lm_api.LoraManagerUnavailable):
                lm_api.hydrate_huggingface(outside, root, 'o/r')
        # Another drive makes os.path.relpath raise ValueError on Windows
        with mock.patch.object(lm_api.os.path, 'relpath', side_effect=ValueError('different drives')), \
             mock.patch.object(lm_api, '_call', side_effect=AssertionError('must not be called')):
            with self.assertRaises(lm_api.LoraManagerUnavailable):
                lm_api.hydrate_huggingface('D:/m.safetensors', root, 'o/r')

    def test_scan_falls_back_to_a_full_scan(self):
        calls = []

        def call(method, route, **kwargs):
            calls.append(kwargs.get('params'))
            if kwargs.get('params'):
                raise lm_api.LoraManagerUnavailable('Unknown model root(s)')
            return {}

        with mock.patch.object(lm_api, '_call', call):
            lm_api.scan('loras', root='/models/loras')
        self.assertEqual(calls, [{'roots': lm_api.lm_path('/models/loras')}, None])
```

(`BackfillTestCase` zeroes `PAUSE_BETWEEN_MODELS`, which otherwise makes every run sleep a second per model.)

- [ ] **Step 2: Run to verify they fail**

Run: `python -m unittest discover -t . -s tests -p "test_hf_identify.py" -v` and `... -p "test_lm_backfill.py" -v`
Expected: ERROR — modules do not exist.

- [ ] **Step 3: Implement `core/lm_api.py`**

```python
"""
Calls to a running Lora Manager through the HTTP API it serves on this same
ComfyUI server. Lora Manager (GPL-3.0) is never imported. Every failure is
raised as LoraManagerUnavailable for the caller to report.
"""

import os
from typing import Any, Dict, List, Optional

TIMEOUT = 300


class LoraManagerUnavailable(Exception):
    pass


def lm_path(path: str) -> str:
    """Absolute, forward slashes, links NOT resolved - the form Lora Manager's cache keys on."""
    return os.path.abspath(path).replace('\\', '/')


def _call(method: str, route: str, **kwargs) -> Dict[str, Any]:
    import requests
    try:
        from server import PromptServer
        url = f'http://127.0.0.1:{PromptServer.instance.port}/api/lm{route}'
    except Exception as e:
        raise LoraManagerUnavailable(f'ComfyUI server not available: {e}')
    try:
        response = requests.request(method, url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as e:
        raise LoraManagerUnavailable(str(e))
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not isinstance(data, (dict, list)):
        data = {}
    if response.status_code >= 400 or (isinstance(data, dict) and data.get('success') is False):
        message = data.get('error') or data.get('message') if isinstance(data, dict) else None
        raise LoraManagerUnavailable(message or f'HTTP {response.status_code} from {route}')
    return data


def available() -> bool:
    try:
        _call('GET', '/model-sources')
        return True
    except LoraManagerUnavailable:
        return False


def scan(prefix: str, root: Optional[str] = None) -> None:
    """Have Lora Manager pick up new files - only under `root` when it knows that root."""
    if root:
        try:
            _call('GET', f'/{prefix}/scan', params={'roots': lm_path(root)})
            return
        except LoraManagerUnavailable:
            pass  # spelled differently there ("Unknown model root"): scan everything
    _call('GET', f'/{prefix}/scan')


def link_huggingface(model_path: str, repo: str) -> None:
    _call('POST', '/set-hf-url', json={'file_path': lm_path(model_path),
                                        'source_url': f'https://huggingface.co/{repo}'})


def hydrate_huggingface(model_path: str, model_root: str, repo: str) -> None:
    """
    Lora Manager reads the repo's model card into the (already linked) sidecar.

    This is Lora Manager's *download* route: it only hydrates because the file
    already exists at model_root/relative_path/filename. A wrong path would
    make it download the file, so a model outside its root is refused here.
    """
    try:
        relative = os.path.relpath(os.path.dirname(model_path), model_root).replace('\\', '/')
    except ValueError:  # another drive on Windows
        raise LoraManagerUnavailable(f'{model_path} is not inside {model_root}')
    if relative == '..' or relative.startswith('../') or not os.path.isfile(model_path):
        raise LoraManagerUnavailable(f'{model_path} is not a file inside {model_root}')
    _call('POST', '/download-model-source', json={
        'platform': 'huggingface', 'repo': repo, 'filename': os.path.basename(model_path),
        'model_root': lm_path(model_root), 'relative_path': '' if relative == '.' else relative,
    })


def save_metadata(prefix: str, model_path: str, fields: Dict[str, Any]) -> None:
    _call('POST', f'/{prefix}/save-metadata', json={'file_path': lm_path(model_path), **fields})


def base_models() -> List[str]:
    """Lora Manager's base-model labels; it answers {"success", "data": {"models": [...]}}."""
    data = _call('GET', '/base-models')
    inner = data.get('data') if isinstance(data, dict) else None
    models = inner.get('models') if isinstance(inner, dict) else None
    return [m for m in models or [] if isinstance(m, str) and m]


def enrich_with_ai(model_paths: List[str]) -> None:
    _call('POST', '/agent/execute/enrich_hf_metadata', json={'model_paths': [lm_path(p) for p in model_paths]})
```

Before moving on, confirm the payloads against the installed Lora Manager: `grep -n "relative_path\|model_root" ../comfyui-lora-manager/py/routes/handlers/model_source_handlers.py` (an empty `relative_path` for a file in the root is accepted in v1.2.5 — `target_dir = base_dir`), `grep -n "def get_base_models" -A30 ../comfyui-lora-manager/py/routes/handlers/base_model_handlers.py` for the `{"success", "data": {"models": [...]}}` envelope `base_models()` parses (the route is registered in `misc_route_registrar.py`, but the handler lives there), and `grep -n "requested_roots" ../comfyui-lora-manager/py/routes/handlers/model_handlers.py` for the `roots` query `scan()` sends.

- [ ] **Step 4: Implement `core/hf_identify.py`**

```python
"""
Which HuggingFace repo a local model file came from - proven by its SHA-256,
never guessed from its name. HuggingFace has no hub-wide hash search, so
candidates come from a link already recorded, the bundled Comfy-Org/Kijai
index, or a name search whose results are then checked file-by-file against
the hash HuggingFace publishes for every LFS file.
"""

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from . import settings
from .sources import hf_index
from .sources.huggingface import HF_API_URL, build_search_queries

logger = logging.getLogger(__name__)

TIMEOUT = 20
SEARCH_REPOS = 3
_NOT_REPOS = {'api', 'datasets', 'spaces', 'collections', 'docs', 'models', 'papers'}
_cards: Dict[str, Optional[Dict[str, Any]]] = {}

# HuggingFace base_model ids -> Lora Manager labels; applied only when the label
# is in Lora Manager's own list (GET /api/lm/base-models)
_BASE_MODELS = (
    ('flux.1-dev', 'Flux.1 D'), ('flux.1-schnell', 'Flux.1 S'), ('flux.1-krea', 'Flux.1 Krea'),
    ('flux.1-kontext', 'Flux.1 Kontext'), ('stable-diffusion-xl', 'SDXL 1.0'),
    ('stable-diffusion-v1-5', 'SD 1.5'), ('qwen-image', 'Qwen'), ('z-image', 'ZImageTurbo'),
    ('ltx-2', 'LTXV2'), ('hunyuanvideo', 'Hunyuan Video'),
)
_BOOKKEEPING_TAG = re.compile(r'^(base_model|license|region|dataset|arxiv|doi|endpoints_compatible|diffusers)(:|$)')


def repo_from_url(url: Optional[str]) -> Optional[str]:
    if not url or settings.provider_for_url(url) != 'huggingface':
        return None
    parts = [p for p in urlparse(url).path.split('/') if p]
    if len(parts) < 2 or parts[0] in _NOT_REPOS:
        return None
    return f'{parts[0]}/{parts[1]}'


def _get(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    import requests
    for attempt in range(2):
        response = requests.get(url, params=params, headers=settings.auth_headers_for_url(url), timeout=TIMEOUT)
        if response.status_code == 429 and attempt == 0:
            time.sleep(min(30, int(response.headers.get('Retry-After') or 10)))
            continue
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RuntimeError(f'HTTP {response.status_code} from HuggingFace')
        return response.json()
    raise RuntimeError('HuggingFace is rate limiting; try again later')


def clear_cache() -> None:
    """Forget fetched model cards; lm_backfill calls this at the start of every run."""
    _cards.clear()


def card(repo: str) -> Optional[Dict[str, Any]]:
    """Model info with every file's LFS sha256 (`?blobs=true`). Cached per run."""
    if repo not in _cards:
        _cards[repo] = _get(f'{HF_API_URL}/models/{repo}', {'blobs': 'true'})
    return _cards[repo]


def _has_file(info: Optional[Dict[str, Any]], sha256: str) -> bool:
    return any(str((s.get('lfs') or {}).get('sha256', '')).lower() == sha256
               for s in (info or {}).get('siblings') or [])


def find_repo(filename: str, sha256: str) -> Optional[Dict[str, Any]]:
    sha256 = (sha256 or '').lower()
    if not re.fullmatch(r'[0-9a-f]{64}', sha256):
        return None
    hit = hf_index.lookup_by_sha256(sha256)
    if hit:
        return {'repo': hit['repo_id'], 'card': card(hit['repo_id']), 'how': 'bundled index'}
    repos: List[str] = []
    for query in build_search_queries(os.path.basename(filename)):
        for model in _get(f'{HF_API_URL}/models', {'search': query, 'limit': SEARCH_REPOS}) or []:
            repo = model.get('id') or model.get('modelId')
            if repo and repo not in repos:
                repos.append(repo)
    for repo in repos[:SEARCH_REPOS * 2]:
        info = card(repo)
        if _has_file(info, sha256):
            return {'repo': repo, 'card': info, 'how': 'hash-verified search'}
    return None


def base_model_label(info: Optional[Dict[str, Any]], allowed: List[str]) -> Optional[str]:
    declared = ((info or {}).get('cardData') or {}).get('base_model') or []
    for base in [declared] if isinstance(declared, str) else declared:
        lowered = str(base).lower()
        for needle, label in _BASE_MODELS:
            if needle in lowered and label in allowed:
                return label
    return None


def card_tags(info: Optional[Dict[str, Any]], limit: int = 10) -> List[str]:
    tags: List[str] = []
    for tag in ((info or {}).get('cardData') or {}).get('tags') or []:
        tag = str(tag).strip().lower()
        if tag and not _BOOKKEEPING_TAG.match(tag) and tag not in tags:
            tags.append(tag)
    return tags[:limit]
```

- [ ] **Step 5: Implement `core/lm_backfill.py`**

```python
"""
Fill empty Lora Manager info for models already on disk, from HuggingFace.

Runs as one background job on the server, so it continues when the dialog is
closed; its last report is kept in data/lm-backfill.json. The README work is
Lora Manager's own (its routes), so the result is exactly its format.
"""

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from . import hf_identify, lm_api, overrides
from .downloader import sha256_file
from .lm_sidecar import lm_target, sidecar_path

logger = logging.getLogger(__name__)

REPORT_FILENAME = 'lm-backfill.json'
PAUSE_BETWEEN_MODELS = 1.0   # be polite to HuggingFace's API

_lock = threading.Lock()
_cancel = threading.Event()
_state: Dict[str, Any] = {'status': 'idle'}

# Lora Manager catalogues a junctioned folder under the first root that reaches it
_CATEGORY_PREFERENCE = ('loras', 'checkpoints', 'embeddings', 'diffusion_models', 'unet',
                        'text_encoders', 'clip', 'vae', 'upscale_models', 'clip_vision', 'controlnet')


def _report_path() -> str:
    return os.path.join(overrides._data_dir(), REPORT_FILENAME)


def _read(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def is_empty(data: Dict[str, Any]) -> bool:
    civitai = data.get('civitai') if isinstance(data.get('civitai'), dict) else {}
    return not civitai.get('id') and not (data.get('modelDescription') or '').strip()


def candidates() -> List[Dict[str, Any]]:
    from .linker import group_models_by_physical_file
    from .scanner import get_model_files
    import folder_paths

    found = []
    for entries in group_models_by_physical_file(get_model_files()).values():
        managed = [e for e in entries if lm_target(e.get('category'))]
        if not managed:
            continue
        managed.sort(key=lambda e: _CATEGORY_PREFERENCE.index(e['category'])
                     if e['category'] in _CATEGORY_PREFERENCE else len(_CATEGORY_PREFERENCE))
        entry = managed[0]
        data = _read(sidecar_path(entry['path']))
        if data is None or not is_empty(data):
            continue
        if data.get('exclude') or data.get('skip_metadata_refresh'):
            continue  # opted out in Lora Manager; its own refresh skips these too
        roots = [r for r in folder_paths.get_folder_paths(entry['category'])
                 if os.path.abspath(entry['path']).startswith(os.path.abspath(r) + os.sep)]
        if not roots:
            continue
        found.append({'path': entry['path'], 'category': entry['category'],
                      'prefix': lm_target(entry['category'])[0], 'root': roots[0], 'sidecar': data})
    return found


def status() -> Dict[str, Any]:
    with _lock:
        if _state.get('status') == 'idle':
            saved = _read(_report_path())
            if saved:
                return {**saved, 'status': 'idle'}
        return json.loads(json.dumps(_state))


def _update(**fields: Any) -> None:
    with _lock:
        _state.update(fields)


def _record(item: Dict[str, Any], outcome: str, **extra: Any) -> None:
    with _lock:
        _state['counts'][outcome] = _state['counts'].get(outcome, 0) + 1
        _state['results'].append({'file': os.path.basename(item['path']), 'category': item['category'],
                                  'outcome': outcome, **extra})
        _state['done'] += 1


def _save_report() -> None:
    with _lock:
        report = json.loads(json.dumps(_state))
    tmp = _report_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(report, handle, indent=1)
    os.replace(tmp, _report_path())


def _fill(item: Dict[str, Any], allowed_bases: List[str]) -> Optional[str]:
    """One model. Returns the outcome; raises on Lora Manager or network failure."""
    path, prefix, data = item['path'], item['prefix'], item['sidecar']
    sha256 = (data.get('sha256') or '').lower()
    if not sha256:
        sha256 = sha256_file(path)
        lm_api.save_metadata(prefix, path, {'sha256': sha256, 'hash_status': 'completed'})

    repo = hf_identify.repo_from_url(data.get('source_url') or data.get('hf_url'))
    info, how = None, 'already linked'
    if not repo:
        found = hf_identify.find_repo(path, sha256)
        if not found:
            return 'not_found'
        repo, info, how = found['repo'], found.get('card'), found['how']
        lm_api.link_huggingface(path, repo)

    words_before = list((data.get('civitai') or {}).get('trainedWords') or [])
    lm_api.hydrate_huggingface(path, item['root'], repo)
    after = _read(sidecar_path(path)) or {}

    extra: Dict[str, Any] = {}
    # Lora Manager's hydration empties trigger words when the README names none
    words_after = list((after.get('civitai') or {}).get('trainedWords') or [])
    merged = words_before + [w for w in words_after if w not in words_before]
    if merged != words_after:
        extra['civitai'] = {'trainedWords': merged}
    if info is None:
        try:
            info = hf_identify.card(repo)
        except Exception as e:  # hydration already succeeded; the extras are a bonus
            logger.debug(f"Model Linker: no model card for {repo}: {e}")
    label = hf_identify.base_model_label(info, allowed_bases)
    if label and (after.get('base_model') or 'Unknown') in ('', 'Unknown'):
        extra['base_model'] = label
    tags = hf_identify.card_tags(info)
    if tags and not after.get('tags'):
        extra['tags'] = tags
    if extra:
        lm_api.save_metadata(prefix, path, extra)
    item['repo'], item['how'] = repo, how
    return 'hydrated'


def _run(use_ai: bool, limit: Optional[int] = None) -> None:
    _cancel.clear()
    hf_identify.clear_cache()  # a card fetched last run may have changed since
    _update(status='running', started_at=time.time(), finished_at=None, total=0, candidates_total=0,
            limit=limit, done=0, current='', counts={}, results=[], use_ai=use_ai, ai_error=None, error=None)
    try:
        if not lm_api.available():
            raise lm_api.LoraManagerUnavailable('Lora Manager is not running in this ComfyUI')
        for prefix in ('loras', 'checkpoints', 'embeddings', 'other'):
            try:
                lm_api.scan(prefix)   # creates sidecars for files it has not catalogued yet
            except lm_api.LoraManagerUnavailable as e:
                logger.debug(f"Model Linker: Lora Manager scan of {prefix} failed: {e}")
        try:
            allowed = lm_api.base_models()
        except lm_api.LoraManagerUnavailable:
            allowed = []
        items = candidates()
        found = len(items)
        if limit:
            items = items[:limit]  # a sample run, to check results before the whole library
        _update(total=len(items), candidates_total=found)
        hydrated = []
        for item in items:
            if _cancel.is_set():
                _update(status='cancelled')
                break
            _update(current=os.path.basename(item['path']))
            try:
                outcome = _fill(item, allowed)
                _record(item, outcome, repo=item.get('repo'), how=item.get('how'))
                if outcome == 'hydrated':
                    hydrated.append(item['path'])
            except Exception as e:
                _record(item, 'failed', error=str(e))
            time.sleep(PAUSE_BETWEEN_MODELS)
        if use_ai and hydrated and not _cancel.is_set():
            # One batch: Lora Manager runs each call as its own background task,
            # so a call per model would start them all at once. Its failure
            # (typically "LLM provider is not configured") is reported on its
            # own - the hydration above already succeeded.
            try:
                lm_api.enrich_with_ai(hydrated)
            except Exception as e:
                _update(ai_error=str(e))
        if _state.get('status') == 'running':
            _update(status='done')
    except Exception as e:
        _update(status='error', error=str(e))
    finally:
        _update(finished_at=time.time(), current='')
        _save_report()


def start(use_ai: bool = False, limit: Optional[int] = None) -> bool:
    with _lock:
        if _state.get('status') == 'running':
            return False
        _state['status'] = 'running'
    threading.Thread(target=_run, args=(use_ai, limit), name='model-linker-lm-backfill', daemon=True).start()
    return True


def cancel() -> None:
    _cancel.set()
```

- [ ] **Step 6: Run the Python tests**

Run: `python -m unittest discover -t . -s tests -p "test_hf_identify.py" -v` and `... -p "test_lm_backfill.py" -v`
Expected: PASS.

- [ ] **Step 7: Hydrate HuggingFace downloads too, and share the client**

In `core/lm_sidecar.py`:
1. Replace the body of `notify_lora_manager` with:

```python
    from . import lm_api
    try:
        # The download landed in a category root: scan only that root
        lm_api.scan(prefix, root=os.path.dirname(model_path))
        if fetch:
            lm_api._call('POST', f'/{prefix}/fetch-civitai', json={'file_path': lm_api.lm_path(model_path)})
    except lm_api.LoraManagerUnavailable as e:
        logger.debug(f"Model Linker: Lora Manager not notified: {e}")
```

2. Replace `on_download_complete` with:

```python
def on_download_complete(record: Dict[str, Any]) -> Optional[str]:
    if not record.get('sha256') or not record.get('path'):
        return None
    from . import hf_identify, lm_api
    from .downloader import category_directory

    path = record['path']
    repo = hf_identify.repo_from_url(record.get('url'))
    # A HuggingFace file is hydrated from its model card below; asking Lora
    # Manager to fetch it from CivitAI first would only cost a futile lookup
    outcome = write_sidecar(path, record.get('category'), record['sha256'], fetch_if_unknown=not repo)
    if outcome != 'written' or not repo:
        return outcome
    try:
        with open(sidecar_path(path), 'r', encoding='utf-8') as handle:
            written = json.load(handle)
    except (OSError, ValueError):
        written = {}
    if (written.get('civitai') or {}).get('id'):
        return outcome  # CivitAI knew the file; its record is the richer one
    try:
        lm_api.link_huggingface(path, repo)
        lm_api.hydrate_huggingface(path, category_directory(record['category']), repo)
    except lm_api.LoraManagerUnavailable as e:
        logger.info(f"Model Linker: Lora Manager could not read {repo}'s model card: {e}")
        return outcome
    return 'written+huggingface'
```

(`category_directory` is imported at call time so the test's patch of `core.downloader.category_directory` applies.)

Add to `WriteTests` in `tests/test_lm_sidecar.py` (the class already patches `notify_lora_manager` and `centralized_sidecars`):

```python
    @unittest.skipUnless(importlib.util.find_spec('requests'), 'requests is not installed')
    def test_huggingface_download_is_hydrated_by_lora_manager(self):
        calls = []
        with mock.patch.object(civitai_meta, 'version_for_sha256', return_value=None), \
             mock.patch('core.lm_api.link_huggingface', lambda *a: calls.append('link')), \
             mock.patch('core.lm_api.hydrate_huggingface', lambda *a: calls.append('hydrate')), \
             mock.patch('core.downloader.category_directory', return_value=self.root):
            outcome = lm_sidecar.on_download_complete({
                'path': self.model, 'category': 'loras', 'sha256': SHA,
                'url': 'https://huggingface.co/o/r/resolve/main/rebel.safetensors'})
        self.assertEqual(outcome, 'written+huggingface')
        self.assertEqual(calls, ['link', 'hydrate'])
        self.assertEqual(self.notified, [('loras', False)])  # no CivitAI fetch for a HF file
```

and add `import importlib.util` to that file's imports.

- [ ] **Step 8: Add the routes**

In `core/routes_downloads.py` (import `lm_backfill`):

```python
    @routes.get("/model_linker/lm-backfill")
    async def backfill_status(request):
        return web.json_response(lm_backfill.status())

    @routes.post("/model_linker/lm-backfill/start")
    async def backfill_start(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        use_ai = bool(data.get('use_ai'))
        limit = data.get('limit')
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            return _bad(web, 'limit must be a positive integer')
        started = lm_backfill.start(use_ai=use_ai, limit=limit)
        return web.json_response({'started': started, 'state': lm_backfill.status()})

    @routes.post("/model_linker/lm-backfill/cancel")
    async def backfill_cancel(request):
        lm_backfill.cancel()
        return web.json_response({'state': lm_backfill.status()})
```

- [ ] **Step 9: Add the Lora Manager section to ⚙ Settings**

Extend `tests/js/test_settings_dialog.mjs` before its `return`:

```js
  check('offers filling empty Lora Manager info', /\/model_linker\/lm-backfill\/start/.test(src));
  check('AI enrichment is opt-in', /use_ai:\s*this\.useAi\.checked/.test(src));
  check('progress is shown as text, not markup', /backfillStatus\.textContent\s*=/.test(src));
  check('an AI enrichment failure is shown', /ai_error/.test(src));
```

Run `node tests/js/run.mjs` — expected: FAIL on the four checks. Then in `web/modules/settings-dialog.js` add a `backfillRow()` to the constructor's children (after `this.lookupRow()`):

```js
    backfillRow() {
        this.useAi = $el("input", { type: "checkbox" });
        this.backfillStatus = $el("div", { style: { fontSize: "12px", opacity: "0.8", whiteSpace: "pre-line" } });
        this.backfillButton = $el("button.model-linker-resolve-btn", {
            textContent: "Fill empty model info from HuggingFace",
            style: { padding: "2px 10px", alignSelf: "flex-start" },
            onclick: () => this.startBackfill(),
        });
        return $el("div", { style: { display: "flex", flexDirection: "column", gap: "6px",
                borderTop: "1px solid var(--border-color)", paddingTop: "12px" } }, [
            $el("strong", { textContent: "Lora Manager" }),
            $el("div", { style: { fontSize: "12px", opacity: "0.6" },
                textContent: "For models whose Lora Manager info is empty: finds the HuggingFace repo each file "
                    + "came from (by its SHA-256, never by name alone), links it, and has Lora Manager read the "
                    + "model card. Runs on the server; you can close this window. Models it cannot identify can "
                    + "be linked by hand in Lora Manager and are filled on the next run." }),
            $el("label", { style: { display: "flex", gap: "8px", alignItems: "center" } }, [
                this.useAi, document.createTextNode("Also use Lora Manager's AI enrichment (base model, tags, usage tips)"),
            ]),
            this.backfillButton,
            this.backfillStatus,
        ]);
    }

    renderBackfill(state) {
        const c = state?.counts || {};
        const summary = `hydrated ${c.hydrated || 0} · not found ${c.not_found || 0} · failed ${c.failed || 0}`
            + (state?.limit ? ` · sample of ${state.total || 0} from ${state.candidates_total || 0} empty models` : "")
            + (state?.ai_error ? `\nAI enrichment did not run: ${state.ai_error}` : "");
        if (state?.status === "running") {
            this.backfillStatus.textContent = `Working… ${state.done || 0} / ${state.total || "?"}`
                + `${state.current ? ` — ${state.current}` : ""}\n${summary}`;
        } else if (state?.finished_at) {
            const when = new Date(state.finished_at * 1000).toLocaleString();
            this.backfillStatus.textContent = `Last run ${state.status === "error" ? `failed: ${state.error}` : state.status} (${when})\n${summary}`;
        } else {
            this.backfillStatus.textContent = "";
        }
        this.backfillButton.disabled = state?.status === "running";
        clearTimeout(this.backfillTimer);
        if (state?.status === "running" && this.element.style.display !== "none") {
            this.backfillTimer = setTimeout(async () => {
                try { this.renderBackfill(await this.request("/model_linker/lm-backfill")); } catch (e) { }
            }, 2000);
        }
    }

    async startBackfill() {
        try {
            const result = await this.request("/model_linker/lm-backfill/start", { use_ai: this.useAi.checked });
            this.renderBackfill(result.state);
        } catch (error) {
            notifyError("Could not start filling model info", error);
        }
    }
```

In `show()`, after rendering the settings, also call `this.renderBackfill(await this.request("/model_linker/lm-backfill"))` inside the same `try`.

- [ ] **Step 10: Run the whole suite**

Run: `python tests/run.py` — expected: all pass.

- [ ] **Step 11: Verify against the real library**

This writes into real Lora Manager sidecars, so it starts with a **sample** and stops for the user (hand-off: "Stop and ask the user before … the first fill run against the whole library").

Restart ComfyUI (Lora Manager running).
1. **Back up first.** Copy every `*.metadata.json` under the model folders (they are small) into a dated folder in the session scratchpad, keeping relative paths, so any sidecar can be restored. Note one linked-but-empty case to watch, e.g. `E:\models\loras\ltx\ltx23\ltx-2.3_Deforum_Evolution_v2.metadata.json`.
2. **Sample, no AI:** `curl -s -X POST http://127.0.0.1:8188/model_linker/lm-backfill/start -H "Content-Type: application/json" -d '{"use_ai":false,"limit":5}'`, then poll `curl -s http://127.0.0.1:8188/model_linker/lm-backfill` until `status` is `done`. Expect `total: 5`, `candidates_total` around the counts measured above, and five `results`.
3. For two `hydrated` results, diff the backup against the new sidecar and check in Lora Manager: README description and example images, `metadata_source: "source:huggingface"`, `autov3` and earlier trigger words still present, nothing else lost. For `not_found` results, confirm they are genuinely not on HuggingFace (e.g. your own `gt\` trainings).
4. **Sample with AI** (only if Lora Manager has an LLM configured): `-d '{"use_ai":true,"limit":10}'`. The sample's `not_found` models come first again, so the larger limit reaches fresh ones; confirm base model and tags appear on what this run hydrated. Without an LLM, the report shows `ai_error` and the run still ends `done`.
5. **Stop.** Report steps 2–4 to the user (counts, the two before/after diffs, any failures) and wait for a go-ahead. Restore from the backup anything that is wrong.
6. Only after the go-ahead: ⚙ Settings → Fill empty model info from HuggingFace (no limit), without AI. Close the window; reopen after a minute: progress continues. When done, the watched case shows its README in Lora Manager; spot-check three `hydrated` and three `not_found` entries from `data/lm-backfill.json`.

- [ ] **Step 12: Commit**

```bash
git add core/lm_api.py core/hf_identify.py core/lm_backfill.py core/lm_sidecar.py core/routes_downloads.py web/modules/settings-dialog.js tests/test_hf_identify.py tests/test_lm_backfill.py tests/test_lm_sidecar.py tests/js/test_settings_dialog.mjs
git commit -m "Fill empty Lora Manager info from HuggingFace, identifying repos by hash"
```
