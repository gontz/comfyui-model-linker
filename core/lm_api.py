"""
Calls to a running Lora Manager through the HTTP API it serves on this same
ComfyUI server. Lora Manager (GPL-3.0) is never imported. Every failure is
raised as LoraManagerUnavailable for the caller to report.
"""

import os
from typing import Any, Dict, List, Optional

TIMEOUT = 300


class LoraManagerUnavailable(Exception):
    """
    `status_code` is the HTTP status Lora Manager answered with, when it
    answered at all - None for a request that never got a response (a
    timeout, a connection error). `scan()` uses this to tell "this root
    is spelled differently" (400) apart from "Lora Manager isn't reachable
    right now", which must never be treated the same way.

    `unreachable` is True only for the two `_call` failures that never got
    an HTTP response at all - no server to ask, or the request itself
    raised (a timeout, a connection error). It is deliberately *not* just
    "`status_code is None`": `hydrate_huggingface`'s own local validation
    (a model outside its root, a zero-byte file) also raises with no
    `status_code`, and that is not Lora Manager being unreachable - it is
    this call refusing to even try, for a reason that has nothing to do
    with connectivity. `lm_backfill._run` uses this flag to decide whether
    a per-model failure means "stop the whole run" (Lora Manager itself has
    stopped answering) or "record it and move on" (an ordinary failure,
    still a real response from a server that is still up).
    """

    def __init__(self, message: str, status_code: Optional[int] = None, unreachable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.unreachable = unreachable


def lm_path(path: str) -> str:
    """Absolute, forward slashes, links NOT resolved - the form Lora Manager's cache keys on."""
    return os.path.abspath(path).replace('\\', '/')


def _call(method: str, route: str, **kwargs) -> Dict[str, Any]:
    import requests
    try:
        from server import PromptServer
        url = f'http://127.0.0.1:{PromptServer.instance.port}/api/lm{route}'
    except Exception as e:
        raise LoraManagerUnavailable(f'ComfyUI server not available: {e}', unreachable=True)
    try:
        response = requests.request(method, url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as e:
        # Timed out, connection refused, connection reset, DNS failure, ... -
        # no response was ever received, as opposed to a response that
        # happens to carry an error status (handled below, not unreachable).
        raise LoraManagerUnavailable(str(e), unreachable=True)
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not isinstance(data, (dict, list)):
        data = {}
    if response.status_code >= 400 or (isinstance(data, dict) and data.get('success') is False):
        message = data.get('error') or data.get('message') if isinstance(data, dict) else None
        raise LoraManagerUnavailable(message or f'HTTP {response.status_code} from {route}',
                                     status_code=response.status_code)
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
        except LoraManagerUnavailable as e:
            # Only "this root is spelled differently there" (HTTP 400,
            # "Unknown model root(s)") falls back to a full scan. A timeout
            # or connection error means Lora Manager isn't reachable at all
            # right now, and scanning "everything" would just fail the same
            # way a second time.
            if e.status_code != 400:
                raise
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
    if os.path.getsize(model_path) <= 0:
        # Lora Manager only treats a same-named file as "already there" when
        # it has real bytes; a zero-byte file makes it download the repo's
        # copy over this path instead of reading the card into the sidecar.
        raise LoraManagerUnavailable(f'{model_path} is empty; hydrating would make Lora Manager download it')
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
