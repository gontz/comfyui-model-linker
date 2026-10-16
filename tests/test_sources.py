"""
Download sources. Network calls are replaced with fakes; the bundled
HuggingFace index is read as shipped.
"""

import logging
import unittest
from unittest import mock

try:
    import requests
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core.matcher import is_variant_token
from core.sources import civarchive, civitai, hf_index, huggingface
from core.sources.civitai import parse_civitai_url
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


class CivitaiTests(unittest.TestCase):
    def test_parses_real_hosts_only(self):
        parsed = parse_civitai_url('https://civitai.com/models/123?modelVersionId=456')
        self.assertEqual(parsed, {'model_id': 123, 'version_id': 456})
        for url in ('https://civitai.com.evil.example/models/1',
                    'https://evil.example/civitai.com/models/1',
                    'http://civitai.com/models/1'):
            self.assertIsNone(parse_civitai_url(url), url)


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


class MalformedTokenTests(unittest.TestCase):
    """A token with a CR/LF makes requests raise InvalidHeader with the whole
    Authorization value in its message - which must never reach the log."""

    TOKEN = 'tok_LEAKME0123456789\r\nX'

    def setUp(self):
        huggingface._search_cache.clear()
        civitai._search_cache.clear()
        self.addCleanup(huggingface._search_cache.clear)
        self.addCleanup(civitai._search_cache.clear)
        # tests/run.py disables logging for the whole run; these tests read it
        self.addCleanup(logging.disable, logging.root.manager.disable)
        logging.disable(logging.NOTSET)

    @staticmethod
    def invalid_header(url, params=None, headers=None, **kwargs):
        raise requests.exceptions.InvalidHeader(f"Invalid header value {(headers or {}).get('Authorization')!r}")

    def test_no_source_logs_the_token(self):
        calls = (
            lambda: huggingface.search_huggingface_for_file('a.safetensors', token=self.TOKEN),
            lambda: huggingface.search_huggingface('a', token=self.TOKEN),
            lambda: civitai.search_civitai_for_file('a.safetensors', api_key=self.TOKEN),
            lambda: civitai.search_civitai('a', api_key=self.TOKEN),
            lambda: civitai.search_civitai_by_hash(SHA, api_key=self.TOKEN),
        )
        with mock.patch.object(requests, 'get', self.invalid_header):
            for call in calls:
                with self.assertLogs('core.sources', level='DEBUG') as logs:
                    logging.getLogger('core.sources').debug('marker')
                    call()
                self.assertTrue(len(logs.output) > 1, 'the failure was not logged at all')
                for line in logs.output:
                    self.assertNotIn('LEAKME', line)

    def test_a_failed_repo_listing_does_not_log_the_token(self):
        def get(url, params=None, headers=None, **kwargs):
            if '/tree/' in url:
                self.invalid_header(url, headers=headers)
            return FakeResponse(200, [{'id': 'org/repo'}])

        with mock.patch.object(requests, 'get', get), \
             self.assertLogs('core.sources', level='DEBUG') as logs:
            huggingface.search_huggingface_for_file('a.safetensors', token=self.TOKEN)
        self.assertTrue(any('org/repo' in line for line in logs.output))
        for line in logs.output:
            self.assertNotIn('LEAKME', line)


class SearchCacheTests(unittest.TestCase):
    """A rate limit or an outage is not an answer: only a complete search
    that genuinely found nothing may be cached as 'not found'."""

    def setUp(self):
        huggingface._search_cache.clear()
        civitai._search_cache.clear()
        self.addCleanup(huggingface._search_cache.clear)
        self.addCleanup(civitai._search_cache.clear)

    def test_huggingface_rate_limit_is_not_cached(self):
        with mock.patch.object(requests, 'get', lambda url, **kw: FakeResponse(429, None)):
            self.assertIsNone(huggingface.search_huggingface_for_file('a.safetensors', exact_only=True))
        self.assertEqual(huggingface._search_cache, {})

    def test_huggingface_failed_repo_listing_is_not_cached(self):
        def get(url, **kw):
            return FakeResponse(429, None) if '/tree/' in url else FakeResponse(200, [{'id': 'org/repo'}])
        with mock.patch.object(requests, 'get', get):
            self.assertIsNone(huggingface.search_huggingface_for_file('a.safetensors', exact_only=True))
        self.assertEqual(huggingface._search_cache, {})

    def test_huggingface_genuine_miss_is_cached(self):
        def get(url, **kw):
            return FakeResponse(200, [{'type': 'file', 'path': 'b.safetensors'}]) if '/tree/' in url \
                else FakeResponse(200, [{'id': 'org/repo'}])
        with mock.patch.object(requests, 'get', get):
            self.assertIsNone(huggingface.search_huggingface_for_file('a.safetensors', exact_only=True))
        self.assertEqual(list(huggingface._search_cache.values()), [None])

    def test_civitai_rate_limit_is_not_cached(self):
        with mock.patch.object(requests, 'get', lambda url, **kw: FakeResponse(429, None)):
            self.assertIsNone(civitai.search_civitai_for_file('a.safetensors', exact_only=True))
        self.assertEqual(civitai._search_cache, {})

    def test_civitai_genuine_miss_is_cached(self):
        with mock.patch.object(requests, 'get', lambda url, **kw: FakeResponse(200, {'items': []})):
            self.assertIsNone(civitai.search_civitai_for_file('a.safetensors', exact_only=True))
        self.assertEqual(list(civitai._search_cache.values()), [None])


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
