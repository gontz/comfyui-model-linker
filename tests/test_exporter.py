"""Export: links fit to share, and hashing confined to model folders."""

import hashlib
import os
import time
import unittest

from core import exporter
from support import LibraryTestCase


class LinkTests(unittest.TestCase):
    def test_secrets_are_stripped(self):
        self.assertEqual(exporter.strip_secrets('https://civitai.com/api/download/models/1?token=abc&type=Model'),
                         'https://civitai.com/api/download/models/1?type=Model')
        self.assertEqual(exporter.strip_secrets('https://x.example/a?SIG=1&Signature=2'), 'https://x.example/a')

    def test_existing_link_wins_and_is_stripped(self):
        # No **kwargs: a leftover fetch_size=False raises TypeError here, which
        # lookup_links swallows into None - and this test fails on it
        def chain(filename, category='', workflow_url='', sha256=''):
            return {'url': workflow_url or 'https://other', 'source': 'workflow', 'url_source': 'workflow'}
        links = exporter.lookup_links([{'filename': 'a.safetensors', 'category': 'loras',
                                        'existing_url': 'https://civitai.com/x?token=secret'}], chain)
        self.assertEqual(links[0]['url'], 'https://civitai.com/x')

    def test_no_search_without_permission(self):
        # Recorded, not raised: lookup_links turns a chain exception into None
        asked = []

        def chain(filename, category='', workflow_url='', sha256=''):
            asked.append((filename, workflow_url))
            return {'url': workflow_url or 'https://found', 'source': 'workflow', 'url_source': 'workflow'}
        links = exporter.lookup_links([
            {'filename': 'a.safetensors', 'category': 'loras'},
            {'filename': 'b.safetensors', 'category': 'loras', 'existing_url': 'https://h/b?token=t'},
            {},
        ], chain, search=False)
        self.assertIsNone(links[0])
        self.assertEqual(links[1]['url'], 'https://h/b')
        self.assertIsNone(links[2])
        self.assertEqual(asked, [('b.safetensors', 'https://h/b')])

    def test_markdown_lists_each_model_once(self):
        md = exporter.build_model_links_markdown([
            {'name': 'a.safetensors', 'url': 'https://h/a', 'directory': 'loras'},
            {'name': 'a.safetensors', 'url': 'https://h/a', 'directory': 'loras'},
            {'name': 'b.safetensors', 'url': 'https://h/b', 'directory': 'checkpoints'},
        ])
        self.assertIn('## Model links', md)
        self.assertEqual(md.count('[a.safetensors]'), 1)
        self.assertIn('[b.safetensors](https://h/b)', md)


class HashJobTests(LibraryTestCase):
    def test_hash_then_look_up(self):
        base = self.add_models('loras', ['h.safetensors'], ['.safetensors'])
        path = os.path.join(base, 'h.safetensors')
        expected = hashlib.sha256(open(path, 'rb').read()).hexdigest()
        job = exporter.start_hash_job(path, lambda sha, name: {'url': 'https://civitai.com/x?token=t', 'source': 'civitai'})
        deadline = time.time() + 5
        while exporter.get_hash_job(job)['status'] not in ('done', 'error') and time.time() < deadline:
            time.sleep(0.02)
        result = exporter.get_hash_job(job)
        self.assertEqual(result['sha256'], expected)
        self.assertEqual(result['result']['url'], 'https://civitai.com/x')
