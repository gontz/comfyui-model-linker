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
from .sources import hf_index, model_list
from .sources.civitai import CIVITAI_API_URL
from .sources.huggingface import HF_API_URL, build_search_queries, get_huggingface_download_url

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
    queries = build_search_queries(filename)
    repos: List[str] = []
    last_error = None
    any_succeeded = False
    for query in queries:
        try:
            models = _get(f'{HF_API_URL}/models', {'search': query, 'limit': MAX_REPOS}) or []
        except Exception as e:
            # One query failing (a timeout, a transient 5xx) must not drop
            # whatever a different, successful query in this same call
            # already found - but if every query fails, that is a total
            # outage, not a one-off blip, and the caller's own per-source
            # try/except (in search_candidates) needs to see it as an error
            # rather than this function returning a silently empty list, as
            # if HuggingFace had simply found nothing. any_succeeded tracks
            # the request itself, not whether it turned up a repo - a query
            # that legitimately found nothing must not be mistaken for one
            # that never ran.
            logger.debug(f"Model Linker: HuggingFace search for {query!r} failed: {e}")
            last_error = e
            continue
        any_succeeded = True
        for model in models:
            repo = model.get('id') or model.get('modelId')
            if repo and repo not in repos:
                repos.append(repo)
        if len(repos) >= MAX_REPOS:
            break
    if queries and not any_succeeded and last_error is not None:
        raise last_error
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
    """
    Every ComfyUI-Manager list entry with a URL - local, no network.

    search_model_list_multiple() pre-filters by raw SequenceMatcher similarity
    (a 40% floor on the normalised name) before a caller ever sees a
    candidate, which is exactly the "ranks filenames backwards" problem the
    banded matcher exists to avoid (see core/matcher.py). Every entry is
    handed to search_candidates() unfiltered instead, so it gets scored by
    the same calculate_filename_confidence() as every other source - the
    overall result cap still applies once everything is ranked together.
    """
    found = []
    for entry in model_list._load_model_list():
        name, url = entry.get('filename', ''), entry.get('url', '')
        if not name or not url:
            continue
        found.append({
            'source': 'model_list', 'filename': name, 'url': url,
            'name': entry.get('name', ''), 'directory': entry.get('save_path', ''),
            'size': entry.get('size', ''),
        })
    return found


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
