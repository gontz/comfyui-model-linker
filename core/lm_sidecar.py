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
