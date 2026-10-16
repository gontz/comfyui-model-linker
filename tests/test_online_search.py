"""
Searching for similar models online: every candidate scored with the banded
matcher, different models labelled, sources failing independently.
"""

import unittest
from unittest import mock

try:
    import requests
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import online_search, settings

HF_SHA = 'ab' * 32


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def fake_get(routes, sent):
    def get(url, params=None, headers=None, timeout=None):
        sent.append((url, dict(headers or {})))
        for prefix, response in routes.items():
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return response
        return FakeResponse({}, 404)
    return get


# Longest prefix first: the tree URL also starts with the search URL
HF_ROUTES = {
    'https://huggingface.co/api/models/org/qwen/tree/main': FakeResponse([
        {'type': 'file', 'path': 'split/qwen3vl_8b_bf16.safetensors', 'size': 10, 'lfs': {'size': 16e9, 'oid': HF_SHA}},
        {'type': 'file', 'path': 'split/qwen3vl_4b_int8.safetensors', 'lfs': {'size': 4e9, 'oid': 'cd' * 32}},
        {'type': 'file', 'path': 'README.md', 'size': 1},
        {'type': 'directory', 'path': 'split'},
    ]),
    'https://huggingface.co/api/models': FakeResponse([{'id': 'org/qwen'}]),
}
CIVITAI_ROUTE = {
    'https://civitai.com/api/v1/models': FakeResponse({'items': [
        {'id': 7, 'name': 'Qwen VL tune', 'type': 'Checkpoint', 'modelVersions': [
            {'id': 70, 'baseModel': 'Qwen', 'files': [
                {'name': 'qwen3vl_8b_fp8.safetensors', 'downloadUrl': 'https://civitai.com/api/download/models/70',
                 'sizeKB': 8e6, 'hashes': {'SHA256': 'EF' * 32}}]}]},
        {'id': 8, 'name': 'A style', 'type': 'LORA', 'modelVersions': [
            {'id': 80, 'files': [{'name': 'qwen3vl_style.safetensors',
                                  'downloadUrl': 'https://civitai.com/api/download/models/80', 'sizeKB': 100}]}]},
    ]}),
}


class SearchTests(unittest.TestCase):
    def search(self, routes, category='checkpoints'):
        self.sent = []
        with mock.patch.object(requests, 'get', fake_get(routes, self.sent)), \
             mock.patch.object(online_search, '_index_candidates', return_value=[]), \
             mock.patch.object(online_search, '_model_list_candidates', return_value=[]):
            return online_search.search_candidates('qwen3vl_8b_int8.safetensors', category)

    def test_same_model_ranks_above_other_size(self):
        candidates = self.search({**HF_ROUTES, **CIVITAI_ROUTE})['candidates']
        names = [c['filename'] for c in candidates]
        self.assertLess(names.index('qwen3vl_8b_bf16.safetensors'), names.index('qwen3vl_4b_int8.safetensors'))
        by_name = {c['filename']: c for c in candidates}
        self.assertTrue(by_name['qwen3vl_4b_int8.safetensors']['different_model'])
        self.assertFalse(by_name['qwen3vl_8b_bf16.safetensors']['different_model'])

    def test_only_model_files_and_the_right_civitai_type(self):
        names = [c['filename'] for c in self.search({**HF_ROUTES, **CIVITAI_ROUTE})['candidates']]
        self.assertNotIn('README.md', names)
        self.assertNotIn('qwen3vl_style.safetensors', names)  # a LoRA, but a checkpoint is missing
        self.assertIn('qwen3vl_8b_fp8.safetensors', names)

    def test_hashes_come_along_for_verification(self):
        by_name = {c['filename']: c for c in self.search({**HF_ROUTES, **CIVITAI_ROUTE})['candidates']}
        self.assertEqual(by_name['qwen3vl_8b_bf16.safetensors']['sha256'], HF_SHA)
        self.assertEqual(by_name['qwen3vl_8b_fp8.safetensors']['sha256'], 'ef' * 32)
        self.assertEqual(by_name['qwen3vl_8b_bf16.safetensors']['url'],
                         'https://huggingface.co/org/qwen/resolve/main/split/qwen3vl_8b_bf16.safetensors')

    def test_a_failing_source_does_not_hide_the_others(self):
        routes = {**HF_ROUTES, 'https://civitai.com/api/v1/models': requests.ConnectionError('down')}
        result = self.search(routes)
        self.assertTrue(any(c['source'] == 'huggingface' for c in result['candidates']))
        self.assertTrue(any('CivitAI' in e for e in result['errors']))

    def test_tokens_go_only_to_their_own_host(self):
        with mock.patch.object(settings, 'get_token', side_effect=lambda p: f'{p}-token'):
            self.search({**HF_ROUTES, **CIVITAI_ROUTE})
        for url, headers in self.sent:
            auth = headers.get('Authorization', '')
            if url.startswith('https://huggingface.co/'):
                self.assertEqual(auth, 'Bearer huggingface-token')
            elif url.startswith('https://civitai.com/'):
                self.assertEqual(auth, 'Bearer civitai-token')


class LocalCandidateTests(unittest.TestCase):
    def test_bundled_index_is_searched_without_network(self):
        with mock.patch.object(requests, 'get', side_effect=AssertionError('no network here')):
            found = online_search._index_candidates('flux1-dev-fp8-e4m3fn.safetensors')
        self.assertTrue(any(c['filename'] == 'flux1-dev-fp8.safetensors' for c in found))
