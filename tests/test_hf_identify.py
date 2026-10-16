"""Identifying a local file's HuggingFace repo: by hash, never by name alone."""

import unittest
from unittest import mock

try:
    import requests
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import civitai_meta, hf_identify

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

    def test_the_empty_file_hash_is_never_identified(self):
        """Fix round 1, item 4: a zero-byte file's hash is never looked up -
        it would describe whatever unrelated repo happens to collide on it,
        not this file. Checked before any network call."""
        with mock.patch.object(hf_identify.hf_index, 'lookup_by_sha256',
                               side_effect=AssertionError('must not be called')), \
             mock.patch.object(requests, 'get', side_effect=AssertionError('must not be called')):
            self.assertIsNone(hf_identify.find_repo('m.safetensors', civitai_meta.EMPTY_FILE_SHA256))
            self.assertIsNone(hf_identify.find_repo('m.safetensors', civitai_meta.EMPTY_FILE_SHA256.upper()))

    def test_a_card_fetch_failure_does_not_abort_checking_the_rest(self):
        """Fix round 1, item 11: one repo's card being unreachable must not
        stop verifying the remaining search hits."""
        with mock.patch.object(hf_identify.hf_index, 'lookup_by_sha256', return_value=None), self.routes({
            'https://huggingface.co/api/models/owner/broken': FakeResponse({}, 500),
            'https://huggingface.co/api/models/owner/right': FakeResponse(card_payload('owner/right', SHA)),
            'https://huggingface.co/api/models': FakeResponse([{'id': 'owner/broken'}, {'id': 'owner/right'}]),
        }):
            found = hf_identify.find_repo('m.safetensors', SHA)
        self.assertEqual(found['repo'], 'owner/right')

    def test_bundled_index_match_survives_a_card_fetch_failure(self):
        """Fix round 1, item 11: the hash already proved the match; a card
        that can't be fetched is only a missed bonus, not a lost identification."""
        hit = {'repo_id': 'Comfy-Org/x', 'path': 'a/m.safetensors'}
        with mock.patch.object(hf_identify.hf_index, 'lookup_by_sha256', return_value=hit), self.routes({
            'https://huggingface.co/api/models/Comfy-Org/x': FakeResponse({}, 500),
        }):
            found = hf_identify.find_repo('m.safetensors', SHA)
        self.assertEqual((found['repo'], found['how'], found['card']), ('Comfy-Org/x', 'bundled index', None))

    def test_retry_after_as_an_http_date_falls_back_to_ten_seconds(self):
        """Fix round 1, item 10: HuggingFace may send an HTTP-date instead
        of a number of seconds; that must not raise."""
        responses = [FakeResponse({}, 429, headers={'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT'}),
                     card_payload('owner/x', SHA)]

        def get(url, params=None, headers=None, timeout=None):
            value = responses.pop(0)
            return value if isinstance(value, FakeResponse) else FakeResponse(value)

        with mock.patch.object(requests, 'get', get), mock.patch.object(hf_identify.time, 'sleep') as sleep:
            result = hf_identify._get('https://huggingface.co/api/models/owner/x')
        sleep.assert_called_once_with(10)
        self.assertEqual(result['id'], 'owner/x')

    def test_a_malformed_token_exception_does_not_chain_onto_the_raised_error(self):
        """Fix round 1, item 9: `from None` so a traceback never surfaces
        the original, token-bearing exception via exception chaining."""
        with mock.patch.object(requests, 'get',
                               side_effect=requests.exceptions.InvalidHeader(
                                   'Authorization: Bearer SECRET-TOKEN-VALUE')):
            with self.assertRaises(RuntimeError) as ctx:
                hf_identify._get('https://huggingface.co/api/models/owner/x')
        self.assertTrue(ctx.exception.__suppress_context__)
        self.assertIsNone(ctx.exception.__cause__)
        self.assertNotIn('SECRET-TOKEN-VALUE', str(ctx.exception))


class CardTests(unittest.TestCase):
    def test_base_model_label_only_from_lora_managers_own_list(self):
        card = {'cardData': {'base_model': ['black-forest-labs/FLUX.1-dev']}}
        self.assertEqual(hf_identify.base_model_label(card, ['Flux.1 D', 'SDXL 1.0']), 'Flux.1 D')
        self.assertIsNone(hf_identify.base_model_label(card, ['SDXL 1.0']))
        self.assertIsNone(hf_identify.base_model_label({'cardData': {'base_model': 'someone/odd-model'}}, ['Flux.1 D']))

    def test_tags_drop_hub_bookkeeping(self):
        card = {'cardData': {'tags': ['LoRA', 'flux', 'base_model:x/y', 'license:mit', 'flux']}}
        self.assertEqual(hf_identify.card_tags(card), ['lora', 'flux'])
