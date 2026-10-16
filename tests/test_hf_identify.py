"""Identifying a local file's HuggingFace repo: by hash, never by name alone."""

import unittest
from unittest import mock

try:
    import requests
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import hf_identify

SHA = 'ab' * 32


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload


def card_payload(repo, sha, card_data=None):
    return {'id': repo, 'cardData': card_data or {}, 'tags': [],
            'siblings': [{'rfilename': 'm.safetensors', 'lfs': {'sha256': sha, 'size': 1}}]}


class RepoFromUrlTests(unittest.TestCase):
    def test_parses_repo_pages_and_file_links(self):
        for url in ('https://huggingface.co/owner/repo', 'https://huggingface.co/owner/repo/',
                    'https://huggingface.co/owner/repo/resolve/main/sub/x.safetensors',
                    'https://huggingface.co/owner/repo/blob/main/x.safetensors'):
            self.assertEqual(hf_identify.repo_from_url(url), 'owner/repo', url)

    def test_rejects_other_hosts_and_non_model_pages(self):
        for url in ('https://huggingface.co.evil.example/owner/repo', 'https://huggingface.co/datasets/a/b',
                    'https://huggingface.co/spaces/a/b', 'https://huggingface.co/owner', '', None):
            self.assertIsNone(hf_identify.repo_from_url(url), url)


class FindRepoTests(unittest.TestCase):
    def setUp(self):
        hf_identify._cards.clear()
        self.addCleanup(hf_identify._cards.clear)

    def routes(self, mapping):
        def get(url, params=None, headers=None, timeout=None):
            for prefix, response in mapping.items():
                if url.startswith(prefix):
                    return response
            return FakeResponse({}, 404)
        return mock.patch.object(requests, 'get', get)

    def test_hash_verified_search_result(self):
        with mock.patch.object(hf_identify.hf_index, 'lookup_by_sha256', return_value=None), self.routes({
            'https://huggingface.co/api/models/owner/right': FakeResponse(card_payload('owner/right', SHA)),
            'https://huggingface.co/api/models/owner/wrong': FakeResponse(card_payload('owner/wrong', 'cd' * 32)),
            'https://huggingface.co/api/models': FakeResponse([{'id': 'owner/wrong'}, {'id': 'owner/right'}]),
        }):
            found = hf_identify.find_repo('boogu_image_turbo_bf16.safetensors', SHA)
        self.assertEqual(found['repo'], 'owner/right')
        self.assertEqual(found['how'], 'hash-verified search')

    def test_same_name_without_the_hash_is_not_a_match(self):
        with mock.patch.object(hf_identify.hf_index, 'lookup_by_sha256', return_value=None), self.routes({
            'https://huggingface.co/api/models/owner/wrong': FakeResponse(card_payload('owner/wrong', 'cd' * 32)),
            'https://huggingface.co/api/models': FakeResponse([{'id': 'owner/wrong'}]),
        }):
            self.assertIsNone(hf_identify.find_repo('m.safetensors', SHA))

    def test_bundled_index_answers_first(self):
        hit = {'repo_id': 'Comfy-Org/x', 'path': 'a/m.safetensors'}
        with mock.patch.object(hf_identify.hf_index, 'lookup_by_sha256', return_value=hit), self.routes({
            'https://huggingface.co/api/models/Comfy-Org/x': FakeResponse(card_payload('Comfy-Org/x', SHA)),
        }):
            found = hf_identify.find_repo('m.safetensors', SHA)
        self.assertEqual((found['repo'], found['how']), ('Comfy-Org/x', 'bundled index'))


class CardTests(unittest.TestCase):
    def test_base_model_label_only_from_lora_managers_own_list(self):
        card = {'cardData': {'base_model': ['black-forest-labs/FLUX.1-dev']}}
        self.assertEqual(hf_identify.base_model_label(card, ['Flux.1 D', 'SDXL 1.0']), 'Flux.1 D')
        self.assertIsNone(hf_identify.base_model_label(card, ['SDXL 1.0']))
        self.assertIsNone(hf_identify.base_model_label({'cardData': {'base_model': 'someone/odd-model'}}, ['Flux.1 D']))

    def test_tags_drop_hub_bookkeeping(self):
        card = {'cardData': {'tags': ['LoRA', 'flux', 'base_model:x/y', 'license:mit', 'flux']}}
        self.assertEqual(hf_identify.card_tags(card), ['lora', 'flux'])
