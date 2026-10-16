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

from . import civitai_meta, settings
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
        try:
            response = requests.get(url, params=params, headers=settings.auth_headers_for_url(url), timeout=TIMEOUT)
        except Exception as e:
            # Never str(e): some exceptions (requests.exceptions.InvalidHeader on
            # a malformed token) embed the full Authorization header, token
            # included - same reasoning as civitai_meta._get_json's guard.
            # `from None` so the token-bearing original is never chained onto
            # a traceback this exception's message is built from.
            raise RuntimeError(type(e).__name__) from None
        if response.status_code == 429 and attempt == 0:
            try:
                # HuggingFace may send an HTTP-date here instead of seconds;
                # either way this is a wait, never worth failing the lookup over.
                wait = int(response.headers.get('Retry-After') or 10)
            except ValueError:
                wait = 10
            time.sleep(min(30, wait))
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
    if not re.fullmatch(r'[0-9a-f]{64}', sha256) or sha256 == civitai_meta.EMPTY_FILE_SHA256:
        return None
    hit = hf_index.lookup_by_sha256(sha256)
    if hit:
        # The hash already proved the file; the card is only ever a bonus
        # (base model, tags) on top of an identification that's already
        # complete, so a failure fetching it must not undo the match.
        try:
            info = card(hit['repo_id'])
        except Exception as e:
            logger.debug(f"Model Linker: no model card for {hit['repo_id']}: {e}")
            info = None
        return {'repo': hit['repo_id'], 'card': info, 'how': 'bundled index'}
    repos: List[str] = []
    for query in build_search_queries(os.path.basename(filename)):
        for model in _get(f'{HF_API_URL}/models', {'search': query, 'limit': SEARCH_REPOS}) or []:
            repo = model.get('id') or model.get('modelId')
            if repo and repo not in repos:
                repos.append(repo)
    for repo in repos[:SEARCH_REPOS * 2]:
        try:
            info = card(repo)
        except Exception as e:
            # One repo's card being unreachable must not stop checking the
            # rest - the hash match could still be the next one.
            logger.debug(f"Model Linker: no model card for {repo}: {e}")
            continue
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
