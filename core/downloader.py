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
