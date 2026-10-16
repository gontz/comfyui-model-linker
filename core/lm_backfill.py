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
from .categories import canonical_category
from .downloader import sha256_file
from .lm_sidecar import lm_target, sidecar_path

logger = logging.getLogger(__name__)

REPORT_FILENAME = 'lm-backfill.json'
STATUS_RESULTS = 20  # results status() returns; the report on disk keeps every one
PAUSE_BETWEEN_MODELS = 1.0   # be polite to HuggingFace's API

_lock = threading.Lock()
_cancel = threading.Event()
_state: Dict[str, Any] = {'status': 'idle'}

# Lora Manager catalogues a junctioned folder under the first root that
# reaches it. Canonical names only - 'unet'/'clip' are aliases already
# covered by 'diffusion_models'/'text_encoders'.
_CATEGORY_PREFERENCE = ('loras', 'checkpoints', 'embeddings', 'diffusion_models',
                        'text_encoders', 'vae', 'upscale_models', 'clip_vision', 'controlnet')


def _preference_rank(category: Optional[str]) -> int:
    canon = canonical_category(category)
    return _CATEGORY_PREFERENCE.index(canon) if canon in _CATEGORY_PREFERENCE else len(_CATEGORY_PREFERENCE)


def _report_path() -> str:
    return os.path.join(overrides._data_dir(), REPORT_FILENAME)


def _read(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _blank(value: Any) -> bool:
    """Empty, for Lora Manager's own "empty" spellings of notes/usage_tips."""
    text = str(value or '').strip()
    return text in ('', '{}')


def is_empty(data: Dict[str, Any]) -> bool:
    civitai = data.get('civitai') if isinstance(data.get('civitai'), dict) else {}
    return not civitai.get('id') and not (data.get('modelDescription') or '').strip()


def _is_unset_base_model(value: Any) -> bool:
    """
    Shared by the AI-eligibility gate and the write-a-bonus-label check, so
    the two can never drift apart: "this model has no base model yet" must
    mean exactly the same thing in both places.
    """
    return (value or 'Unknown') in ('', 'Unknown')


def candidates() -> List[Dict[str, Any]]:
    from .linker import group_models_by_physical_file
    from .scanner import get_model_files
    import folder_paths

    found = []
    for entries in group_models_by_physical_file(get_model_files()).values():
        managed = [e for e in entries if lm_target(e.get('category'))]
        if not managed:
            continue
        managed.sort(key=lambda e: _preference_rank(e['category']))
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
    """
    The current run, or - once idle - the last persisted report, verbatim.

    A previous run's own status ('done'/'error'/'cancelled') must survive a
    server restart: overwriting it with 'idle' here would hide a failure or
    a cancellation behind a status that claims nothing has ever run.
    'from_report' marks that this came from disk rather than a live run, so
    a caller (and the Settings dialog) can tell the two apart - it is set
    only when there is no live state in this process at all, i.e. right
    after a restart; a report read while a run is actually in progress, or
    right after one finished in this same process, carries no such flag.

    A restart losing the whole Python process can catch a run between its
    per-model save (status still 'running') and the next one - the
    persisted report is then stuck saying 'running' forever, since nothing
    is left alive to ever finish it. That status is relabelled
    'interrupted' here, so the UI shows it as what it is instead of
    "still working" with a disabled button and a poll loop that never ends.

    Only the last STATUS_RESULTS results are returned, with `results_total`
    counting them all: the dialog polls this every second or two, and a run
    over a whole library would otherwise resend every result each time. The
    full list stays in the live state and in the report on disk.
    """
    with _lock:
        result = None
        if _state.get('status') == 'idle':
            saved = _read(_report_path())
            if saved:
                result = {**saved, 'from_report': True}
                if result.get('status') == 'running':
                    result['status'] = 'interrupted'
        if result is None:
            result = json.loads(json.dumps(_state))
    results = result.get('results') if isinstance(result.get('results'), list) else []
    result['results_total'] = len(results)
    result['results'] = results[-STATUS_RESULTS:]
    return result


def _update(**fields: Any) -> None:
    with _lock:
        _state.update(fields)


def _record(item: Dict[str, Any], outcome: str, **extra: Any) -> None:
    with _lock:
        _state['counts'][outcome] = _state['counts'].get(outcome, 0) + 1
        _state['results'].append({'file': os.path.basename(item['path']), 'category': item['category'],
                                  'outcome': outcome, **extra})
        _state['done'] += 1


def _note_ai_skip() -> None:
    with _lock:
        _state['counts']['ai_skipped'] = _state['counts'].get('ai_skipped', 0) + 1


def _save_report() -> None:
    with _lock:
        report = json.loads(json.dumps(_state))
    tmp = _report_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(report, handle, indent=1)
    os.replace(tmp, _report_path())


def _fill(item: Dict[str, Any], allowed_bases: List[str]) -> Optional[str]:
    """One model. Returns the outcome; raises on Lora Manager or network failure."""
    path, prefix = item['path'], item['prefix']

    # Re-read rather than trust the snapshot candidates() took: time passes
    # between listing candidates and reaching this one (other models' own
    # searches/hydrations, the pause between models), during which the user
    # could have resolved, excluded, or opted this model out in Lora Manager.
    data = _read(sidecar_path(path))
    if data is None or data.get('exclude') or data.get('skip_metadata_refresh') or not is_empty(data):
        return 'skipped'

    source_url = data.get('source_url') or data.get('hf_url')
    repo = hf_identify.repo_from_url(source_url)
    if source_url and not repo:
        # A link to another site (ModelScope, Tensor.Art, ...) the user chose
        # on purpose. hf_identify can neither confirm nor replace it, so it is
        # never searched, never relinked, and never hydrated from here.
        return 'linked_elsewhere'

    sha256 = (data.get('sha256') or '').lower()
    if not sha256:
        sha256 = sha256_file(path)
        lm_api.save_metadata(prefix, path, {'sha256': sha256, 'hash_status': 'completed'})

    info, how = None, 'already linked'
    if not repo:
        found = hf_identify.find_repo(path, sha256)
        if not found:
            return 'not_found'
        repo, info, how = found['repo'], found.get('card'), found['how']
        lm_api.link_huggingface(path, repo)

    # Decided from the state *before* hydration: Lora Manager's AI enrichment
    # (if used later) can replace trigger words, notes, usage_tips AND base
    # model wholesale - even for an already-linked model - which would undo
    # the merge-back below for any model that already had its own. Models
    # with none of those are the only ones the AI batch may touch. The same
    # "unset" predicate used to decide whether a bonus base_model may be
    # written below is reused here, so the two can never say different things
    # about the same sidecar.
    civitai_before = data.get('civitai') if isinstance(data.get('civitai'), dict) else {}
    words_before = list(civitai_before.get('trainedWords') or [])
    ai_eligible = (not words_before and _blank(data.get('notes')) and _blank(data.get('usage_tips'))
                   and _is_unset_base_model(data.get('base_model')))

    # Also decided from *before* hydration: is_empty() only looks at
    # civitai.id/modelDescription, so a sidecar can already carry
    # metadata_source: 'source:huggingface' from an earlier partial state
    # and still be a candidate. Comparing only the post-hydration value
    # would then call a hydration that genuinely wrote nothing new
    # "hydrated" simply because that value was already there.
    was_hf_sourced = data.get('metadata_source') == 'source:huggingface'
    was_described = bool((data.get('modelDescription') or '').strip())

    if words_before:
        # Recorded *before* the hydrate call itself, not only before the
        # merge-back save afterward: if hydrate_huggingface raises after
        # Lora Manager has already cleared the words on its own side (our
        # 300s timeout, or a dropped connection while Lora Manager carries
        # on regardless), the 'failed' outcome below must still carry
        # something to restore from - the merge-back save never even gets
        # attempted in that case, so recording it only there would leave
        # nothing.
        item['trainedWords_before'] = words_before

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
    if label and _is_unset_base_model(after.get('base_model')):
        extra['base_model'] = label
    tags = hf_identify.card_tags(info)
    if tags and not after.get('tags'):
        extra['tags'] = tags
    if extra:
        lm_api.save_metadata(prefix, path, extra)

    item['repo'], item['how'] = repo, how
    # Lora Manager's own hydration route can answer success while writing
    # nothing - HuggingFace unreachable, no README, a gated card, a
    # repo/filename mismatch. Only count it as hydrated when it actually
    # changed something that wasn't already there before this call.
    hydrated = ((after.get('metadata_source') == 'source:huggingface' and not was_hf_sourced)
                or (bool((after.get('modelDescription') or '').strip()) and not was_described))
    item['ai_eligible'] = hydrated and ai_eligible
    return 'hydrated' if hydrated else 'no_card'


def _run(use_ai: bool, limit: Optional[int] = None) -> None:
    _cancel.clear()
    hf_identify.clear_cache()  # a card fetched last run may have changed since
    _update(status='running', started_at=time.time(), finished_at=None, total=0, candidates_total=0,
            limit=limit, done=0, current='', counts={}, results=[], use_ai=use_ai, ai_error=None, error=None)
    try:
        if not lm_api.available():
            raise lm_api.LoraManagerUnavailable('Lora Manager is not running in this ComfyUI')
        # Deliberately does not call lm_api.scan() here. A scan with no root
        # (one per Lora Manager prefix, unavoidable since this job's own
        # candidates aren't known yet) makes Lora Manager catalogue its
        # *entire* library - every file under every registered root for that
        # prefix, not just the ones this run will touch - and can rewrite
        # file_path in sidecars it had already catalogued differently.
        # candidates() below only ever considers models that already have a
        # sidecar; this job fills what Lora Manager already catalogues, it
        # does not catalogue anything on its own. notify_lora_manager (after
        # a download) still calls lm_api.scan(), but always scoped to the
        # one root the new file landed in.
        try:
            allowed = lm_api.base_models()
        except lm_api.LoraManagerUnavailable:
            allowed = []
        items = candidates()
        found = len(items)
        if limit:
            items = items[:limit]  # a sample run, to check results before the whole library
        _update(total=len(items), candidates_total=found)
        # Saved once here too, not only after each model: without this, a
        # restart before the very first per-model save leaves whatever the
        # *previous* run last wrote (status 'done', its own stale total) on
        # disk - this run would be entirely invisible, rather than showing
        # up as 'running' (and, after that restart, 'interrupted').
        try:
            _save_report()
        except OSError as e:
            logger.warning(f"Model Linker: could not save the backfill report: {e}")
        hydrated = []
        stop_error = None
        for item in items:
            if _cancel.is_set():
                _update(status='cancelled')
                break
            _update(current=os.path.basename(item['path']))
            try:
                outcome = _fill(item, allowed)
                ai_skip = outcome == 'hydrated' and not item.get('ai_eligible')
                extra = {'repo': item.get('repo'), 'how': item.get('how')}
                if ai_skip:
                    extra['ai_skipped'] = True
                _record(item, outcome, **extra)
                if ai_skip:
                    _note_ai_skip()
                if outcome == 'hydrated' and item.get('ai_eligible'):
                    hydrated.append(item['path'])
            except Exception as e:
                extra = {'error': str(e)}
                if item.get('trainedWords_before') is not None:
                    # Set in _fill as soon as there were words to protect,
                    # before hydrate_huggingface was even called - whether
                    # this failed during that call (Lora Manager cleared the
                    # words on its side regardless) or during the
                    # merge-back save afterward, there would otherwise be
                    # nothing left to recover the words from.
                    extra['trainedWords_before'] = item['trainedWords_before']
                _record(item, 'failed', **extra)
                # A LoraManagerUnavailable that never got a response at all -
                # a timeout, connection refused, or no server to ask - means
                # Lora Manager itself has stopped answering, not that this
                # one model failed. Its own README processing can block the
                # whole aiohttp event loop (observed on the real library:
                # model ~320 froze Lora Manager, and this run then spent
                # three hours recording the identical timeout-then-refused
                # failure for every remaining model while nothing was
                # written for any of them). Stop here instead - the
                # untouched models stay empty, and the next run (after a
                # restart) picks them up, same as any other candidate.
                if isinstance(e, lm_api.LoraManagerUnavailable) and e.unreachable:
                    filename = os.path.basename(item['path'])
                    stop_error = (f"Lora Manager stopped responding while processing {filename}; "
                                  "the run was stopped. Restart ComfyUI and run again to continue.")
            # Persisted after every model, not only at the end, so an
            # interrupted run (a server restart, a crash) still leaves a
            # report listing every sidecar actually touched so far. A
            # transient lock on the file (antivirus, an indexer, a sync
            # client) must not abort the run over it - the final save in
            # the outer `finally` below still has every result collected
            # so far and is not guarded the same way.
            try:
                _save_report()
            except OSError as e:
                logger.warning(f"Model Linker: could not save the backfill report: {e}")
            if stop_error:
                break
            time.sleep(PAUSE_BETWEEN_MODELS)
        if stop_error:
            raise lm_api.LoraManagerUnavailable(stop_error)
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
