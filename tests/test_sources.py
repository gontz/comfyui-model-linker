"""
Download sources. Network calls are replaced with fakes; the bundled
HuggingFace index is read as shipped.
"""

import unittest

try:
    import requests  # noqa: F401
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core.matcher import is_variant_token
from core.sources import civarchive, hf_index
from core.sources.huggingface import build_search_queries, parse_huggingface_url

SHA = 'df7c757437ef3696e76ee5cc18c063681478639132547b6292b0b4d773814ba5'


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class VariantTokenTests(unittest.TestCase):
    def test_precision_and_quant_tokens(self):
        for token in ('fp8', 'bf16', 'fp16', 'q4', 'e4m3fn', 'int8', 'Q8'):
            self.assertTrue(is_variant_token(token), token)
        for token in ('flux1', 'dev', '8b', 'qwen3vl'):
            self.assertFalse(is_variant_token(token), token)


class HuggingFaceTests(unittest.TestCase):
    def test_search_queries_strip_precision_suffix(self):
        self.assertEqual(build_search_queries('flux1-dev-fp8_e4m3fn.safetensors'), ['flux1-dev-fp8_e4m3fn', 'flux1-dev'])
        self.assertEqual(build_search_queries('clip_l.safetensors'), ['clip_l'])

    def test_parses_real_hosts_only(self):
        parsed = parse_huggingface_url('https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/sub%20dir/a.safetensors')
        self.assertEqual(parsed, {'repo': 'Comfy-Org/flux1-dev', 'branch': 'main', 'filename': 'sub dir/a.safetensors'})
        for url in ('https://huggingface.co.evil.example/a/b/resolve/main/c.safetensors',
                    'https://evil.example/huggingface.co/a/b/resolve/main/c.safetensors',
                    'http://huggingface.co/a/b/resolve/main/c.safetensors'):
            self.assertIsNone(parse_huggingface_url(url), url)


class IndexTests(unittest.TestCase):
    def test_finds_comfy_org_file_before_kijai(self):
        result = hf_index.lookup_by_filename('flux1-dev-fp8.safetensors')
        self.assertEqual(result['repo_id'], 'Comfy-Org/flux1-dev')
        self.assertTrue(result['url'].startswith('https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/'))
        self.assertEqual(len(result['sha256']), 64)

    def test_hash_lookup_round_trips(self):
        by_name = hf_index.lookup_by_filename('flux1-dev-fp8.safetensors')
        by_hash = hf_index.lookup_by_sha256(by_name['sha256'].upper())
        self.assertEqual(by_hash['path'], by_name['path'])
        self.assertEqual(by_hash['match_type'], 'hash')

    def test_folder_prefix_is_ignored(self):
        self.assertIsNotNone(hf_index.lookup_by_filename('FLUX\\flux1-dev-fp8.safetensors'))


class CivArchiveTests(unittest.TestCase):
    def setUp(self):
        self._get = civarchive.requests.get
        civarchive._cache.clear()

    def tearDown(self):
        civarchive.requests.get = self._get
        civarchive._cache.clear()

    def test_prefers_a_live_huggingface_mirror(self):
        payload = {'model': {'name': 'Styles', 'type': 'LORA'}, 'files': [
            {'filename': 'a.safetensors', 'url': 'https://civitai.com/api/download/models/1', 'source': 'civitai'},
            {'filename': 'a.safetensors', 'url': 'https://huggingface.co/x/y/resolve/main/gone.safetensors',
             'source': 'huggingface', 'deletedAt': '2025-01-01'},
            {'filename': 'a.safetensors', 'url': 'https://huggingface.co/x/y/resolve/main/gated.safetensors',
             'source': 'huggingface', 'is_gated': True},
            {'filename': 'a.safetensors', 'url': 'https://huggingface.co/x/y/resolve/main/a.safetensors',
             'source': 'huggingface'},
        ]}
        civarchive.requests.get = lambda url, **kw: FakeResponse(200, payload)
        result = civarchive.lookup_by_sha256(SHA, 'a.safetensors')
        self.assertEqual(result['url'], 'https://huggingface.co/x/y/resolve/main/a.safetensors')
        self.assertEqual(result['match_type'], 'hash')

    def test_bad_hashes_make_no_request(self):
        calls = []
        civarchive.requests.get = lambda url, **kw: calls.append(url)
        self.assertIsNone(civarchive.lookup_by_sha256('not-a-hash'))
        self.assertIsNone(civarchive.lookup_by_sha256('0' * 64))
        self.assertEqual(calls, [])

    def test_a_failed_request_is_not_cached(self):
        # An outage is not an answer: the next lookup must ask again
        def down(url, **kw):
            raise civarchive.requests.ConnectionError('down')
        civarchive.requests.get = down
        self.assertIsNone(civarchive.lookup_by_sha256(SHA))
        self.assertNotIn(SHA, civarchive._cache)
        civarchive.requests.get = lambda url, **kw: FakeResponse(503, {})
        self.assertIsNone(civarchive.lookup_by_sha256(SHA))
        self.assertNotIn(SHA, civarchive._cache)
