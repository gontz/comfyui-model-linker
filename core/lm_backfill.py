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
