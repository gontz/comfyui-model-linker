"""
Server-side settings: API tokens for HuggingFace and CivitAI, and options that
govern work the server does on its own (background lookups).

Tokens never go back to the browser: the UI only sees whether one is set, where
it came from, and its last four characters.
"""

import json
import logging
import os
import threading
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from . import overrides

logger = logging.getLogger(__name__)

SETTINGS_FILENAME = 'settings.json'

# provider -> (settings key, environment variable fallback)
PROVIDERS = {
    'huggingface': ('hf_token', 'HF_TOKEN'),
    'civitai': ('civitai_key', 'CIVITAI_API_TOKEN'),
}

# Options the user can switch. Booleans only; anything else is rejected.
OPTION_DEFAULTS = {
    # Look up download sources for missing models in the background after each
    # analysis. Sends model filenames (and hashes) to HuggingFace and CivitAI.
    'auto_lookup': True,
}

_lock = threading.Lock()


def get_settings_path() -> str:
    """Beside the saved overrides, in the extension's gitignored data folder."""
    return os.path.join(overrides._data_dir(), SETTINGS_FILENAME)


def load_settings() -> Dict[str, Any]:
    path = get_settings_path()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning(f"Model Linker: could not read settings: {e}")
        return {}


def save_settings(updates: Dict[str, Any]) -> None:
    """Merge updates into the settings file; empty strings remove a key."""
    with _lock:
        settings = load_settings()
        for key, value in updates.items():
            if value is None or value == '':
                settings.pop(key, None)
            else:
                settings[key] = value

        path = get_settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + '.tmp'
        # Owner-only permissions where the OS supports them (no-op on Windows)
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
        os.replace(tmp_path, path)


def get_token(provider: str) -> Optional[str]:
    """Token for 'huggingface' or 'civitai': saved setting first, then env var."""
    if provider not in PROVIDERS:
        return None
    key, env_var = PROVIDERS[provider]
    token = load_settings().get(key) or os.environ.get(env_var)
    return token.strip() if isinstance(token, str) and token.strip() else None


def get_option(name: str) -> Any:
    """An option's saved value, or its default. Unknown names raise KeyError."""
    default = OPTION_DEFAULTS[name]
    value = load_settings().get(name, default)
    return value if isinstance(value, bool) else default


def public_view() -> Dict[str, Any]:
    """What the UI may see: whether a token is set, where from, and a hint."""
    settings = load_settings()
    view = {}
    for provider, (key, env_var) in PROVIDERS.items():
        saved = settings.get(key)
        if not isinstance(saved, str):
            saved = None
        env = os.environ.get(env_var)
        token = saved or env
        view[provider] = {
            'set': bool(token),
            'source': 'settings' if saved else ('env' if env else None),
            'env_var': env_var,
            'hint': f'…{token[-4:]}' if token and len(token) > 8 else ('set' if token else ''),
        }
    view['options'] = {name: get_option(name) for name in OPTION_DEFAULTS}
    return view


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith('.' + domain)


def provider_for_url(url: str) -> Optional[str]:
    """
    Which token a URL may receive, decided by its actual host. Download URLs
    can come from a shared workflow, so a substring check would let
    https://evil.example/huggingface.co/... collect the user's token.
    """
    try:
        parsed = urlparse(url or '')
    except ValueError:
        return None
    if parsed.scheme != 'https':
        return None
    host = (parsed.hostname or '').lower()
    if _host_matches(host, 'huggingface.co') or host == 'hf.co':
        return 'huggingface'
    if _host_matches(host, 'civitai.com'):
        return 'civitai'
    return None


def auth_headers_for_url(url: str) -> Dict[str, str]:
    """Authorization header for a download/search URL, if we hold a token for its host."""
    provider = provider_for_url(url or '')
    token = get_token(provider) if provider else None
    return {'Authorization': f'Bearer {token}'} if token else {}


def check_token(provider: str) -> Dict[str, Any]:
    """Validate the stored token against the provider's API."""
    import requests

    token = get_token(provider)
    if not token:
        return {'ok': False, 'error': 'No token set'}

    if provider == 'huggingface':
        url = 'https://huggingface.co/api/whoami-v2'
    elif provider == 'civitai':
        url = 'https://civitai.com/api/v1/me'
    else:
        return {'ok': False, 'error': f'Unknown provider: {provider}'}

    try:
        response = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=10)
    except Exception as e:
        # Never str(e): a malformed token (e.g. an embedded newline that
        # slipped past .strip()) makes requests raise with the full header,
        # token included, in the exception's message.
        logger.warning(f"Model Linker: could not reach {provider}: {type(e).__name__}")
        return {'ok': False, 'error': f'Could not reach {provider}'}

    if response.status_code in (401, 403):
        return {'ok': False, 'error': 'Token was rejected'}
    if response.status_code != 200:
        return {'ok': False, 'error': f'Unexpected response ({response.status_code})'}

    try:
        data = response.json()
    except ValueError:
        data = {}
    user = data.get('name') or data.get('username') or data.get('fullname')
    return {'ok': True, 'user': user}
