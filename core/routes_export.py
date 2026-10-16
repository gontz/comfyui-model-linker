"""Routes for the Export dialog. Registered with the download routes (needs `requests`)."""

import asyncio

from . import exporter
from .reveal import locate_model
from .settings import get_option
from .source_chain import find_by_hash, find_download_source

MAX_MODELS = 500
LOOKUP_FIELDS = ('filename', 'category', 'existing_url', 'sha256')


def _bad(web, message):
    return web.json_response({'error': message}, status=400)


async def _body(request):
    try:
        return await request.json()
    except Exception:
        return None


def register(routes, web) -> None:
    @routes.post("/model_linker/export/lookup")
    async def lookup(request):
        data = await _body(request)
        models = data.get('models') if isinstance(data, dict) else None
        if not isinstance(models, list) or len(models) > MAX_MODELS:
            return _bad(web, f'models must be a list of at most {MAX_MODELS}')
        # One entry per item, malformed ones included, so links[i] answers models[i]
        clean = [{k: m[k] for k in LOOKUP_FIELDS if isinstance(m.get(k), str)} if isinstance(m, dict) else {}
                 for m in models]
        # A press of "Find links" is the user asking; otherwise the auto_lookup
        # switch decides whether filenames go to HuggingFace and CivitAI. A model
        # that already has a link is only stripped of credentials either way.
        searched = data.get('manual') is True or bool(get_option('auto_lookup'))
        links = await asyncio.to_thread(exporter.lookup_links, clean, find_download_source, searched)
        return web.json_response({'links': links, 'searched': searched})

    @routes.post("/model_linker/export/markdown")
    async def markdown(request):
        data = await _body(request)
        models = data.get('models') if isinstance(data, dict) else None
        if not isinstance(models, list) or len(models) > MAX_MODELS:
            return _bad(web, 'models must be a list')
        clean = []
        for m in models:
            if not isinstance(m, dict):
                return _bad(web, 'each model must be an object')
            name, directory = m.get('name', ''), m.get('directory', '')
            if not isinstance(name, str) or not isinstance(directory, str):
                return _bad(web, 'name and directory must be strings')
            url = m.get('url')
            # A model with no link, or one that is not https, gets no entry -
            # the note is the one place a credential could slip back in, so
            # only a link that has already been through strip_secrets' own
            # scheme check is ever allowed through
            if not isinstance(url, str) or not url.lower().startswith('https://'):
                continue
            clean.append({'name': name, 'url': exporter.strip_secrets(url), 'directory': directory})
        return web.json_response({'markdown': exporter.build_model_links_markdown(clean)})

    @routes.post("/model_linker/export/hash")
    async def start_hash(request):
        data = await _body(request)
        if not isinstance(data, dict):
            return _bad(web, 'Body must be an object')
        path, reason = locate_model(data.get('category') or '', data.get('filename') or '')
        if not path:
            return _bad(web, reason or 'no such model')
        return web.json_response({'job_id': exporter.start_hash_job(path, find_by_hash)})

    @routes.get("/model_linker/export/hash/{job_id}")
    async def hash_status(request):
        job = exporter.get_hash_job(request.match_info['job_id'])
        return web.json_response(job) if job else web.json_response({'error': 'Unknown job'}, status=404)
