"""The order sources are asked in, and what is never contacted."""

import unittest
from unittest import mock

try:
    import requests
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import source_chain

SHA = 'df7c757437ef3696e76ee5cc18c063681478639132547b6292b0b4d773814ba5'


def fail(*args, **kwargs):
    raise AssertionError('this source should not have been asked')


class ChainTests(unittest.TestCase):
    def test_workflow_url_is_returned_without_contacting_it(self):
        # Recorded rather than raised: upstream wraps its HEAD in a bare try
        contacted = []
        record = lambda *a, **k: contacted.append(a)
        with mock.patch.object(requests, 'head', record), mock.patch.object(requests, 'get', record):
            result = source_chain.find_download_source(
                'x.safetensors', category='loras',
                workflow_url='https://huggingface.co/a/b/resolve/main/x.safetensors')
        self.assertEqual(contacted, [])
        self.assertEqual(result['url_source'], 'workflow')
        self.assertEqual(result['source'], 'huggingface')
        self.assertIsNone(result['size'])

    def test_lookalike_host_is_not_called_huggingface(self):
        result = source_chain.find_download_source(
            'x.safetensors', workflow_url='https://huggingface.co.evil.example/a/b/resolve/main/x.safetensors')
        self.assertEqual(result['source'], 'workflow')

    def test_index_answers_before_any_network_search(self):
        with mock.patch.object(source_chain, 'search_huggingface_for_file', fail), \
             mock.patch.object(source_chain, 'search_civitai_for_file', fail):
            result = source_chain.find_download_source('flux1-krea-dev_fp8_scaled.safetensors',
                                                       category='diffusion_models')
        self.assertEqual(result['url_source'], 'hf_index')
        self.assertEqual(result['directory'], 'diffusion_models')

    def test_hash_is_tried_before_name_search(self):
        hit = {'download_url': 'https://civitai.com/api/download/models/42', 'filename': 'private.safetensors',
               'url': 'https://civitai.com/models/7', 'name': 'Private'}
        with mock.patch.object(source_chain, 'search_civitai_by_hash', lambda sha, api_key=None: hit), \
             mock.patch.object(source_chain, 'search_huggingface_for_file', fail), \
             mock.patch.object(source_chain, 'search_civitai_for_file', fail):
            result = source_chain.find_download_source('private_renamed.safetensors', category='loras', sha256=SHA)
        self.assertEqual(result['url'], 'https://civitai.com/api/download/models/42')
        self.assertEqual(result['match_type'], 'hash')
