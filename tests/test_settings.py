"""Server-side settings: tokens never leave the server; options have defaults."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from core import overrides, settings


class SettingsTestCase(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix='model-linker-settings-')
        self._real = overrides._data_dir
        overrides._data_dir = lambda: self.data_dir
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop('HF_TOKEN', None)
        os.environ.pop('CIVITAI_API_TOKEN', None)

    def tearDown(self):
        self._env.stop()
        overrides._data_dir = self._real
        shutil.rmtree(self.data_dir, ignore_errors=True)


class TokenTests(SettingsTestCase):
    def test_saved_token_is_used_but_never_shown(self):
        settings.save_settings({'hf_token': 'hf_abcdefghijklmnop'})
        self.assertEqual(settings.get_token('huggingface'), 'hf_abcdefghijklmnop')
        view = settings.public_view()['huggingface']
        self.assertTrue(view['set'])
        self.assertEqual(view['hint'], '…mnop')
        self.assertNotIn('hf_abcdefghijklmnop', json.dumps(settings.public_view()))

    def test_pasted_whitespace_is_stripped(self):
        settings.save_settings({'civitai_key': '  abcdefghij\n'})
        self.assertEqual(settings.get_token('civitai'), 'abcdefghij')

    def test_empty_string_clears(self):
        settings.save_settings({'hf_token': 'hf_abcdefghijklmnop'})
        settings.save_settings({'hf_token': ''})
        self.assertIsNone(settings.get_token('huggingface'))

    def test_environment_is_the_fallback(self):
        os.environ['HF_TOKEN'] = 'hf_fromenvironment'
        self.assertEqual(settings.get_token('huggingface'), 'hf_fromenvironment')
        self.assertEqual(settings.public_view()['huggingface']['source'], 'env')

    def test_file_lives_in_the_data_directory(self):
        settings.save_settings({'hf_token': 'x' * 12})
        self.assertTrue(os.path.isfile(os.path.join(self.data_dir, 'settings.json')))

    def test_corrupt_file_reads_as_empty(self):
        with open(os.path.join(self.data_dir, 'settings.json'), 'w', encoding='utf-8') as f:
            f.write('[1, 2')
        self.assertEqual(settings.load_settings(), {})
        with open(os.path.join(self.data_dir, 'settings.json'), 'w', encoding='utf-8') as f:
            f.write('[1, 2]')
        self.assertEqual(settings.load_settings(), {})


class HostTests(SettingsTestCase):
    def test_only_real_hosts_receive_tokens(self):
        settings.save_settings({'hf_token': 'hf_abcdefghijklmnop', 'civitai_key': 'civitaikey123'})
        for url in ('https://evil.example/huggingface.co/x', 'https://huggingface.co.evil.example/x',
                    'https://evil.example/?u=civitai.com', 'https://notcivitai.com/x',
                    'http://huggingface.co/x'):
            self.assertEqual(settings.auth_headers_for_url(url), {}, url)
        for url in ('https://huggingface.co/a/b/resolve/main/c', 'https://cdn-lfs.huggingface.co/x',
                    'https://hf.co/x', 'https://civitai.com/api/download/models/1'):
            self.assertIn('Authorization', settings.auth_headers_for_url(url), url)


class OptionTests(SettingsTestCase):
    def test_auto_lookup_defaults_on(self):
        self.assertIs(settings.get_option('auto_lookup'), True)

    def test_auto_lookup_can_be_switched_off(self):
        settings.save_settings({'auto_lookup': False})
        self.assertIs(settings.get_option('auto_lookup'), False)
        self.assertIs(settings.public_view()['options']['auto_lookup'], False)

    def test_unknown_option_is_rejected(self):
        with self.assertRaises(KeyError):
            settings.get_option('no_such_option')
