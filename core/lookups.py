"""
Background lookups of download sources for missing models.

Analysis stays local and fast: it attaches whatever earlier lookups found and
queues the rest. One worker thread asks the source chain, so a slow or
unreachable service delays only that answer - never the dialog, never ComfyUI.
Answers persist in data/lookups.json, so a lookup that finishes after the
dialog was closed is waiting there the next time it opens.
"""

import hashlib
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

# Sentinel put on the queue to ask the worker thread to exit (tests only).
_STOP = object()


def _basename(path: str) -> str:
    return re.split(r'[/\\]', path or '')[-1]


def _sha256_of(missing: Dict[str, Any]) -> str:
    value = str(missing.get('source_hash') or '').strip().lower()
    hash_type = str(missing.get('source_hash_type') or 'sha256').strip().lower()
    return value if hash_type == 'sha256' and _SHA256.match(value) else ''


def _safe_https_url(url: Optional[str]) -> str:
    """The URL, or '' when it is not an https link - the only kind ever sent anywhere."""
    url = url or ''
    try:
        if urlparse(url).scheme != 'https':
            return ''
    except ValueError:
        return ''
    return url


def lookup_key(missing: Dict[str, Any]) -> str:
    """
    Identity of what is being looked for: its hash when known, else category
    and name - but scoped to the workflow's own claimed URL when it has one.

    A workflow can claim any filename, category or hash it likes for its own
    URL, so a URL-carrying lookup must never be allowed to answer - or be
    answered by - another workflow that happens to share that filename or
    hash. The URL prefix isolates it into its own corner of the store while
    still letting that lookup resolve (e.g. to the workflow's own link).
    """
    sha256 = _sha256_of(missing)
    if sha256:
        base = f'sha256:{sha256}'
    else:
        category = canonical_category(missing.get('category')) or 'unknown'
        base = f'{category}:{_basename(missing.get("original_path")).lower()}'
    url = _safe_https_url(missing.get('source_url'))
    if url:
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]
        return f'url:{url_hash}:{base}'
    return base


def _request_for(missing: Dict[str, Any]) -> Dict[str, str]:
    """What the source chain is asked, built only from fields that are safe to send."""
    category = missing.get('category')
    return {
        'filename': _basename(missing.get('original_path')),
        'category': category if canonical_category(category) else '',
        'workflow_url': _safe_https_url(missing.get('source_url')),
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
        """
        The stored answer, or None when unknown, unreadable (not a dict, or a
        checked_at that cannot be parsed as a number), or a negative answer
        that has expired. An entry that cannot be read must be treated as
        absent rather than raising - a single corrupt row must never fail
        /analyze or /model_linker/lookups for every model.
        """
        with self._lock:
            entry = self._load().get(key)
        if not isinstance(entry, dict):
            return None
        try:
            checked_at = float(entry.get('checked_at') or 0)
        except (TypeError, ValueError):
            return None
        if entry.get('result') is None and time.time() - checked_at > NEGATIVE_TTL:
            return None
        return entry

    def put(self, key: str, result: Optional[Dict[str, Any]]) -> None:
        """Write a copy to disk, and only adopt it in memory once the write succeeds."""
        with self._lock:
            data = dict(self._load())
            data[key] = {'result': result, 'checked_at': time.time()}
            path = self._path()
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, indent=1)
            os.replace(tmp, path)
            self._data = data


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

    def wait_idle(self, timeout: float) -> bool:
        """Block until the queue is empty. Returns whether it became idle in time (tests only)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if not self._pending:
                    return True
            time.sleep(0.01)
        with self._lock:
            return not self._pending

    def stop(self) -> None:
        """Ask a running worker thread to exit once idle, and wait for it (tests only)."""
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            self._queue.put(_STOP)
            thread.join(timeout=5)

    def _run(self) -> None:
        while True:
            key = self._queue.get()
            if key is _STOP:
                self._queue.task_done()
                return
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


# Constructed at module load, not lazily: a lazy "create on first use" getter
# raced two threads into building two different worker instances. The
# constructor only sets up state - it starts no thread.
_store = LookupStore()
_worker = LookupWorker(_store, _find)


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
        elif _worker.is_pending(key) or (auto and _worker.enqueue(key, _request_for(missing))):
            missing['lookup_state'] = 'pending'
            pending += 1
        else:
            missing['lookup_state'] = 'idle'
    return pending


def results_for(keys: List[str]) -> Dict[str, Dict[str, Any]]:
    results = {}
    for key in keys:
        if _worker.is_pending(key):
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
    _worker.enqueue(key, _request_for(missing), force=True)
    return key
