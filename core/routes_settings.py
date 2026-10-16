"""Routes for the settings store. Registered from __init__.setup_routes."""

import asyncio
import logging

from . import settings

logger = logging.getLogger(__name__)

# What the browser may write. Tokens are strings; options are booleans.
_TOKEN_KEYS = {'hf_token', 'civitai_key'}


def register(routes, web) -> None:
    @routes.get("/model_linker/settings")
    async def get_settings(request):
        return web.json_response(settings.public_view())

    @routes.post("/model_linker/settings")
    async def post_settings(request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'error': 'Body must be JSON'}, status=400)
        if not isinstance(data, dict):
            return web.json_response({'error': 'Body must be an object'}, status=400)

        updates = {}
        for key, value in data.items():
            if key in _TOKEN_KEYS and isinstance(value, str):
                stripped = value.strip()
                if any(ord(c) < 32 or ord(c) == 127 for c in stripped):
                    return web.json_response({'error': 'Token contains invalid characters'}, status=400)
                updates[key] = stripped
            elif key in settings.OPTION_DEFAULTS and isinstance(value, bool):
                updates[key] = value
            else:
                return web.json_response({'error': f'Unsupported setting: {key}'}, status=400)
        settings.save_settings(updates)
        return web.json_response(settings.public_view())

    @routes.post("/model_linker/settings/check/{provider}")
    async def check(request):
        provider = request.match_info['provider']
        if provider not in settings.PROVIDERS:
            return web.json_response({'ok': False, 'error': 'Unknown provider'}, status=404)
        try:
            result = await asyncio.to_thread(settings.check_token, provider)
        except ImportError:
            result = {'ok': False, 'error': 'The requests package is not installed'}
        return web.json_response(result)
