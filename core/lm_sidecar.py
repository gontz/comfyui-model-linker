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
import tempfile
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from . import civitai_meta, downloader
from .categories import canonical_category

logger = logging.getLogger(__name__)

_EXTENSION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LORA_MANAGER_APP = 'ComfyUI-LoRA-Manager'
_LORA_MANAGER_FOLDER = 'comfyui-lora-manager'

# `mkstemp` always creates its file 0600, regardless of the process umask -
# deliberately, since it is meant for secrets. A landed model sidecar/preview
# is not a secret, so it should come out at the permissions a normal write
# would have produced. Read once at import time, the same safe way the stdlib
# docs describe (`os.umask` sets as a side effect of reading, so the previous
# value is restored immediately): not applicable on Windows, which has no
# umask concept at all.
_UMASK = 0
if os.name != 'nt':
    _UMASK = os.umask(0)
    os.umask(_UMASK)


def _discover_lora_manager_dir() -> str:
    """
    Lora Manager's own folder, found case-insensitively under every
    registered `custom_nodes` root - installs are seen under different
    casings (`comfyui-lora-manager`, `ComfyUI-LoRA-Manager`, ...). Falls back
    to this extension's own parent directory when `folder_paths` has no
    answer (outside a running ComfyUI, e.g. under the test runner).
    """
    roots = []
    try:
        import folder_paths
        roots = list(folder_paths.get_folder_paths('custom_nodes'))
    except Exception:
        roots = []
    if not roots:
        roots = [os.path.dirname(_EXTENSION_DIR)]
    for root in roots:
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for entry in entries:
            if entry.lower() == _LORA_MANAGER_FOLDER:
                return os.path.join(root, entry)
    # Not found under any known root - keep the expected name under the
    # first one, so a later `open()`/`isfile()` simply finds nothing there.
    return os.path.join(roots[0], _LORA_MANAGER_FOLDER)


_LORA_MANAGER_DIR = _discover_lora_manager_dir()

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


def _safe_int(value: Any, default: int = 0) -> int:
    """`int(value)`, but a non-numeric value (a malformed field from CivitAI,
    not ours to validate) falls back to `default` instead of raising."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
        if not isinstance(entry, dict):
            continue
        hashes = entry.get('hashes')
        if not isinstance(hashes, dict):
            continue
        if str(hashes.get('SHA256', '')).lower() == sha256.lower():
            value = hashes.get('AutoV3')
            # Truncated to AutoV3's 12 hex characters, this is the same
            # zero-byte placeholder civitai_meta.EMPTY_FILE_SHA256 guards
            # against above - not a real file's hash, however it got here.
            if (isinstance(value, str) and len(value) >= 12
                    and value[:12].lower() != civitai_meta.EMPTY_FILE_SHA256[:12]):
                return value[:12].lower()
            return None
    return None


def build_sidecar(model_path: str, category: str, sha256: str,
                  version: Optional[Dict[str, Any]] = None, preview_path: str = '',
                  preview_nsfw_level: int = 0, now: Optional[float] = None) -> Dict[str, Any]:
    now = float(time.time() if now is None else now)
    stem = os.path.splitext(os.path.basename(model_path))[0]
    # CivitAI's own shape, not ours to trust: a by-hash answer with no
    # matching model (or one that simply varies) can carry 'model' as
    # something other than an object.
    model_info = (version or {}).get('model')
    model_info = model_info if isinstance(model_info, dict) else {}
    data: Dict[str, Any] = {
        'file_name': stem,
        'model_name': model_info.get('name') or stem,
        'file_path': lm_path(model_path),
        'size': int(os.path.getsize(model_path)),
        'modified': now,
        'sha256': sha256.lower(),
        'base_model': determine_base_model((version or {}).get('baseModel')),
        'preview_url': lm_path(preview_path) if preview_path else '',
        'preview_nsfw_level': _safe_int(preview_nsfw_level, 0),
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
        # Deliberate deviation from the published contract: a JSON `null`
        # here tells Lora Manager "checked, CivitAI has none" and stops it
        # computing the value itself from the file's own header, which it
        # otherwise falls back to. The key is written only when a 12-hex
        # value was actually found, and left out - not nulled - every other
        # time (not checked, or checked and nothing matched).
        found = autov3_for(version, sha256)
        if found:
            data['autov3'] = found
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
    tame = [i for i in stills if _safe_int(i.get('nsfwLevel'), 0) < 4]
    return (tame or stills or [None])[0]


def _rendition(url: str) -> str:
    return url.replace('/original=true/', '/width=450,optimized=true/')


def _is_civitai_image_url(url: str) -> bool:
    """https, and on CivitAI's own image host - checked on the initial URL
    and, separately, re-checked on every hop a redirect actually took."""
    try:
        parsed = urlparse(url or '')
        host = (parsed.hostname or '').lower()
    except ValueError:
        # A malformed authority (e.g. an unbalanced IPv6 literal) can make
        # urlparse, or the lazy `.hostname` property, raise - that is not a
        # CivitAI URL either.
        return False
    return parsed.scheme == 'https' and (host == 'civitai.com' or host.endswith('.civitai.com'))


def _remove_quietly(path: str) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _apply_umask(tmp: str) -> None:
    """
    `tempfile.mkstemp` ignores the process umask and always creates 0600.
    Bring the about-to-be-landed file back to what a plain `open()` write
    would have produced, so a model's sidecar/preview isn't quietly less
    readable than the model file itself. Windows has no umask bits to
    apply, so this is a no-op there.
    """
    if os.name != 'nt':
        try:
            os.chmod(tmp, 0o666 & ~_UMASK)
        except OSError:
            pass


def _land_file(tmp: str, dest: str) -> bool:
    """
    Move `tmp` onto `dest`. True once landed. False only when `dest` was
    already taken (`downloader.DestinationExists`) - Lora Manager's own
    scan, or another download, got there first, which is not a failure to
    report. Any other failure (a permissions problem, an antivirus lock,
    ...) propagates instead of being folded into that same "someone else
    has it" outcome, so a real failure surfaces as one.
    """
    try:
        downloader._rename_no_clobber(tmp, dest)
        return True
    except downloader.DestinationExists:
        return False


def download_preview(model_path: str, image: Dict[str, Any]) -> str:
    """Save CivitAI's 450px rendition as `<stem><ext>`. Returns the path, or '' on any failure."""
    import requests
    url = _rendition(image.get('url') or '')
    if not _is_civitai_image_url(url):
        return ''
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    if ext not in _SAVEABLE_PREVIEW:
        return ''
    dest = os.path.splitext(model_path)[0] + ext
    tmp = None
    try:
        # mkstemp itself - a permissions problem, a full disk - is a failure
        # like any other here, so it sits inside the same try as everything
        # else this function documents as "returns '' on any failure".
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest) or '.', prefix='.model-linker-', suffix='.tmp')
        os.close(fd)
        with requests.get(url, stream=True, timeout=30, allow_redirects=True) as response:
            # A redirect can leave CivitAI's own host, or https, entirely -
            # for the final URL or for any hop along the way - refused
            # before a single byte is written either way, same reasoning as
            # downloader._stream's own redirect check.
            hops = [getattr(response, 'url', None) or url]
            hops += [getattr(hop, 'url', '') or '' for hop in (getattr(response, 'history', None) or ())]
            if not all(_is_civitai_image_url(hop) for hop in hops):
                return ''
            if response.status_code != 200:
                return ''
            size = 0
            with open(tmp, 'wb') as handle:
                for chunk in response.iter_content(256 * 1024):
                    size += len(chunk)
                    if size > MAX_PREVIEW_BYTES:
                        raise ValueError('preview too large')
                    handle.write(chunk)
        _apply_umask(tmp)
        return dest if _land_file(tmp, dest) else ''
    except Exception as e:
        logger.debug(f"Model Linker: preview download failed: {e}")
        return ''
    finally:
        _remove_quietly(tmp)


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


def _write_sidecar_file(path: str, data: Dict[str, Any]) -> bool:
    """
    Write `data` to `path` without ever overwriting a file already there -
    Lora Manager's own scan can write this exact sidecar while this call was
    out talking to CivitAI. A fixed `.tmp` name would also collide with Lora
    Manager's own temp file of the same name, so the temp name is unique.

    True once `path` holds this data; False when something else landed there
    first, in which case `path` is untouched and this call's temp file is gone.
    """
    tmp_dir = os.path.dirname(path) or '.'
    fd, tmp = tempfile.mkstemp(dir=tmp_dir, prefix='.model-linker-', suffix='.tmp')
    os.close(fd)
    try:
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        _apply_umask(tmp)
        return _land_file(tmp, path)
    finally:
        _remove_quietly(tmp)


def notify_lora_manager(prefix: str, model_path: str, fetch: bool = False) -> None:
    """
    Best effort: have a running Lora Manager index the new file now, and fetch
    its info itself when CivitAI could not be asked (its own chain also tries
    CivArchive). Silently does nothing when Lora Manager is not installed.
    """
    from . import lm_api
    try:
        # The download landed in a category root: scan only that root
        lm_api.scan(prefix, root=os.path.dirname(model_path))
        if fetch:
            lm_api._call('POST', f'/{prefix}/fetch-civitai', json={'file_path': lm_api.lm_path(model_path)})
    except lm_api.LoraManagerUnavailable as e:
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
            if preview:
                nsfw = _safe_int(image.get('nsfwLevel'), 0)
            else:
                # Losing the landing race (another download, or Lora
                # Manager itself) doesn't mean there is no preview now - it
                # means someone else's is already there. Record that one
                # rather than leaving preview_url empty next to a model that
                # in fact has an image.
                preview = existing_preview(model_path) or ''

    data = build_sidecar(model_path, category, sha256, version, preview, nsfw)
    if not _write_sidecar_file(path, data):
        # Lora Manager's own scan, or another download, wrote this exact
        # sidecar while CivitAI/the preview were being fetched - leave
        # whatever it wrote alone.
        return 'exists'
    notify_lora_manager(target[0], model_path, fetch=version is None and fetch_if_unknown)
    return 'written'


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
