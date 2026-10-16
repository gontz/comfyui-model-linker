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
    except Exception as e:
        # Never str(e): some exceptions (requests.exceptions.InvalidHeader on
        # a malformed token, or a UnicodeEncodeError raised while encoding a
        # header that contains one) embed the full header value, token
        # included - the same reasoning as settings.check_token's guard
        # against the same leak. Caught broadly, not just RequestException,
        # because the encode failure is a plain UnicodeEncodeError.
        raise CivitaiUnavailable(type(e).__name__)
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
