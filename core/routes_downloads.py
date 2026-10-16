"""
Routes for background lookups and downloads. Registered only when `requests`
imports - see __init__.setup_routes.
"""

import asyncio
import logging

from . import downloader, lm_backfill, lookups, online_search

logger = logging.getLogger(__name__)

MAX_KEYS = 500


def _bad(web, message):
    return web.json_response({'error': message}, status=400)


def _invalid_limit(limit) -> bool:
    """None (the whole library) or a positive int is valid; anything else - a
    bool (an int subclass), zero, a negative number, a string - is not."""
    return limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1)


def register(routes, web) -> None:
    from . import lm_sidecar
    downloader.add_completion_hook(lm_sidecar.on_download_complete)

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

    @routes.post("/model_linker/search")
    async def search_similar(request):
        try:
            data = await request.json()
        except Exception:
            return _bad(web, 'Body must be JSON')
        missing = data.get('missing') if isinstance(data, dict) else None
        if not isinstance(missing, dict) or not isinstance(missing.get('original_path'), str):
            return _bad(web, 'missing.original_path is required')
        category = missing.get('category') if isinstance(missing.get('category'), str) else ''
        result = await asyncio.to_thread(online_search.search_candidates, missing['original_path'], category)
        return web.json_response(result)

    @routes.post("/model_linker/download")
    async def start(request):
        try:
            data = await request.json()
        except Exception:
            return _bad(web, 'Body must be JSON')
        if not isinstance(data, dict):
            return _bad(web, 'Body must be an object')
        url, category, filename = data.get('url'), data.get('category'), data.get('filename')
        if not all(isinstance(v, str) and v for v in (url, category, filename)):
            return _bad(web, 'url, category and filename are required')
        sha256 = data.get('sha256') if isinstance(data.get('sha256'), str) else None
        raw_size = data.get('size')
        # bool is an int subclass - isinstance(True, int) is True - so it is
        # excluded explicitly rather than let `true` become a 1-byte request
        size = raw_size if (isinstance(raw_size, int) and not isinstance(raw_size, bool)
                            and raw_size > 0) else None
        try:
            download_id = await asyncio.to_thread(downloader.start_download, url, category, filename, sha256, size)
        except downloader.DownloadError as e:
            return _bad(web, str(e))
        return web.json_response({'download_id': download_id})

    @routes.get("/model_linker/progress")
    async def all_progress(request):
        return web.json_response(downloader.get_all_progress())

    @routes.get("/model_linker/progress/{download_id}")
    async def one_progress(request):
        record = downloader.get_progress(request.match_info['download_id'])
        return web.json_response(record) if record else web.json_response({'error': 'Unknown download'}, status=404)

    @routes.post("/model_linker/cancel/{download_id}")
    async def cancel(request):
        return web.json_response({'success': downloader.cancel_download(request.match_info['download_id'])})

    @routes.get("/model_linker/lm-backfill")
    async def backfill_status(request):
        return web.json_response(lm_backfill.status())

    @routes.post("/model_linker/lm-backfill/start")
    async def backfill_start(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        use_ai = data.get('use_ai') is True
        limit = data.get('limit')
        if _invalid_limit(limit):
            return _bad(web, 'limit must be a positive integer')
        started = lm_backfill.start(use_ai=use_ai, limit=limit)
        return web.json_response({'started': started, 'state': lm_backfill.status()})

    @routes.post("/model_linker/lm-backfill/cancel")
    async def backfill_cancel(request):
        lm_backfill.cancel()
        return web.json_response({'state': lm_backfill.status()})
