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

    def test_userinfo_is_stripped(self):
        self.assertEqual(exporter.strip_secrets('https://user:pass@host/path'), 'https://host/path')

    def test_userinfo_is_stripped_but_the_port_and_ipv6_brackets_survive(self):
        self.assertEqual(exporter.strip_secrets('https://user:pass@[::1]:8443/path'), 'https://[::1]:8443/path')

    def test_a_fragment_is_stripped(self):
        self.assertEqual(exporter.strip_secrets('https://host/path#token=FRAG'), 'https://host/path')

    def test_aws_presigned_params_are_stripped(self):
        self.assertEqual(
            exporter.strip_secrets('https://host/path?X-Amz-Signature=abc&X-Amz-Credential=def'
                                    '&X-Amz-Security-Token=ghi&foo=bar'),
            'https://host/path?foo=bar')

    def test_old_style_path_params_are_stripped(self):
        self.assertEqual(exporter.strip_secrets('https://host/path;token=abc'), 'https://host/path')

    def test_every_kind_of_credential_together(self):
        self.assertEqual(
            exporter.strip_secrets('https://user:pass@host/path;token=abc?X-Amz-Signature=x&foo=bar#token=FRAG'),
            'https://host/path?foo=bar')

    def test_a_path_param_on_a_non_final_segment_is_stripped(self):
        self.assertEqual(exporter.strip_secrets('https://h/a;token=x/b.safetensors'), 'https://h/a/b.safetensors')

    def test_semicolon_separated_query_pairs_are_split(self):
        self.assertEqual(exporter.strip_secrets('https://host/path?a=1;token=x'), 'https://host/path?a=1')

    def test_access_token_and_google_signature_are_stripped(self):
        self.assertEqual(
            exporter.strip_secrets('https://host/path?access-token=abc&X-Goog-Signature=def'
                                    '&X-Goog-Credential=ghi&foo=bar'),
            'https://host/path?foo=bar')

    def test_a_query_value_containing_semicolons_survives_unchanged(self):
        # Not a credential - a single piece whose own value has ';' in it
        self.assertEqual(exporter.strip_secrets('https://host/path?filter=x;y;z'), 'https://host/path?filter=x;y;z')

    def test_a_bare_query_flag_survives_unchanged(self):
        # No '=' at all - parse_qsl/urlencode used to turn this into '?empty='
        self.assertEqual(exporter.strip_secrets('https://host/path?empty'), 'https://host/path?empty')

    def test_stripping_a_middle_piece_keeps_its_neighbours_joined_correctly(self):
        self.assertEqual(exporter.strip_secrets('https://host/path?a=1&token=x&b=2'), 'https://host/path?a=1&b=2')

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
        with open(path, 'rb') as f:
            expected = hashlib.sha256(f.read()).hexdigest()
        job = exporter.start_hash_job(path, lambda sha, name: {'url': 'https://civitai.com/x?token=t', 'source': 'civitai'})
        deadline = time.time() + 5
        while exporter.get_hash_job(job)['status'] not in ('done', 'error') and time.time() < deadline:
            time.sleep(0.02)
        result = exporter.get_hash_job(job)
        self.assertEqual(result['sha256'], expected)
        self.assertEqual(result['result']['url'], 'https://civitai.com/x')
