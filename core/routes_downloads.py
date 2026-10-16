"""
Routes for background lookups and downloads. Registered only when `requests`
imports - see __init__.setup_routes.
"""

import asyncio
import logging

from . import lookups

logger = logging.getLogger(__name__)

MAX_KEYS = 500


def _bad(web, message):
    return web.json_response({'error': message}, status=400)


def register(routes, web) -> None:
    @routes.post("/model_linker/lookups")
    async def lookup_results(request):
        try:
            data = await request.json()
        except Exception:
            return _bad(web, 'Body must be JSON')
        keys = data.get('keys') if isinstance(data, dict) else None
        if not isinstance(keys, list) or len(keys) > MAX_KEYS:
            return _bad(web, f'keys must be a list of at most {MAX_KEYS} strings')
        keys = [k for k in keys if isinstance(k, str)]
        results = await asyncio.to_thread(lookups.results_for, keys)
        return web.json_response({'results': results})

    @routes.post("/model_linker/lookups/refresh")
    async def lookup_refresh(request):
        try:
            data = await request.json()
        except Exception:
            return _bad(web, 'Body must be JSON')
        missing = data.get('missing') if isinstance(data, dict) else None
        if not isinstance(missing, dict) or not isinstance(missing.get('original_path'), str):
            return _bad(web, 'missing.original_path is required')
        fields = ('original_path', 'category', 'source_url', 'source_hash', 'source_hash_type')
        clean = {k: missing[k] for k in fields if isinstance(missing.get(k), str)}
        key = await asyncio.to_thread(lookups.refresh, clean)
        return web.json_response({'key': key, 'state': 'pending'})
