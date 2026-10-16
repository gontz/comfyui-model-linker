"""Background lookups: keyed, persisted, never blocking, never repeated needlessly."""

import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from core import lookups, overrides, settings

SHA = 'ab' * 32


def missing(name='flux1-dev-fp8.safetensors', category='checkpoints', **extra):
    return {'original_path': name, 'category': category, 'matches': [], **extra}


class LookupTestCase(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix='model-linker-lookups-')
        self._real = overrides._data_dir
        overrides._data_dir = lambda: self.data_dir
        self.found = {}
        self.calls = []
        self.release = threading.Event()
        self.release.set()

        def find(request):
            self.release.wait(5)
            self.calls.append(request)
            if request.get('raise'):
                raise RuntimeError('network down')
            return self.found.get(request['filename'])

        lookups._store = lookups.LookupStore()
        lookups._worker = lookups.LookupWorker(lookups._store, find)

    def tearDown(self):
        lookups._worker.wait_idle(5)
        overrides._data_dir = self._real
        shutil.rmtree(self.data_dir, ignore_errors=True)


class KeyTests(unittest.TestCase):
    def test_sha256_is_preferred(self):
        self.assertEqual(lookups.lookup_key(missing(source_hash=SHA.upper(), source_hash_type='SHA256')),
                         'sha256:' + SHA)

    def test_other_hash_types_are_ignored(self):
        key = lookups.lookup_key(missing(source_hash='1234abcd', source_hash_type='AutoV2'))
        self.assertEqual(key, 'checkpoints:flux1-dev-fp8.safetensors')

    def test_aliases_share_a_key_and_folders_are_dropped(self):
        a = lookups.lookup_key(missing('sub\\T5XXL.safetensors', 'clip'))
        b = lookups.lookup_key(missing('t5xxl.safetensors', 'text_encoders'))
        self.assertEqual(a, b)


class AttachTests(LookupTestCase):
    def test_unknown_models_are_queued_and_answered(self):
        self.found['flux1-dev-fp8.safetensors'] = {'url': 'https://huggingface.co/a/b/resolve/main/x', 'source': 'huggingface'}
        models = [missing()]
        self.assertEqual(lookups.attach_cached(models), 1)
        self.assertEqual(models[0]['lookup_state'], 'pending')
        lookups._worker.wait_idle(5)
        result = lookups.results_for([models[0]['lookup_key']])[models[0]['lookup_key']]
        self.assertEqual(result['state'], 'found')
        self.assertEqual(result['source']['source'], 'huggingface')

    def test_cached_answer_is_attached_without_asking_again(self):
        lookups.attach_cached([missing()])
        lookups._worker.wait_idle(5)
        models = [missing()]
        self.assertEqual(lookups.attach_cached(models), 0)
        self.assertEqual(models[0]['lookup_state'], 'not_found')
        self.assertEqual(len(self.calls), 1)

    def test_answers_survive_a_restart(self):
        lookups.attach_cached([missing()])
        lookups._worker.wait_idle(5)
        lookups._store = lookups.LookupStore()  # a fresh process reading the same file
        models = [missing()]
        lookups.attach_cached(models)
        self.assertEqual(models[0]['lookup_state'], 'not_found')

    def test_negative_answers_expire(self):
        lookups.attach_cached([missing()])
        lookups._worker.wait_idle(5)
        later = lookups.time.time() + lookups.NEGATIVE_TTL + 1
        with mock.patch.object(lookups.time, 'time', return_value=later):
            models = [missing()]
            lookups.attach_cached(models)
        self.assertEqual(models[0]['lookup_state'], 'pending')

    def test_switched_off_queues_nothing(self):
        settings.save_settings({'auto_lookup': False})
        models = [missing()]
        self.assertEqual(lookups.attach_cached(models), 0)
        self.assertEqual(models[0]['lookup_state'], 'idle')
        lookups._worker.wait_idle(5)
        self.assertEqual(self.calls, [])

    def test_models_with_a_perfect_local_match_are_not_looked_up(self):
        models = [missing(matches=[{'confidence': 100.0, 'category_mismatch': False}])]
        lookups.attach_cached(models)
        self.assertEqual(models[0]['lookup_state'], 'not_needed')
        lookups._worker.wait_idle(5)
        self.assertEqual(self.calls, [])

    def test_duplicate_references_are_looked_up_once(self):
        self.release.clear()
        models = [missing(), missing(), missing('sub/flux1-dev-fp8.safetensors')]
        self.assertEqual(lookups.attach_cached(models), 3)
        self.release.set()
        lookups._worker.wait_idle(5)
        self.assertEqual(len(self.calls), 1)

    def test_a_failure_is_not_remembered(self):
        request = lookups._request_for(missing())
        request['raise'] = True
        lookups._worker.enqueue('k', request, force=True)
        lookups._worker.wait_idle(5)
        self.assertIsNone(lookups._store.get('k'))
        self.assertFalse(lookups._worker.is_pending('k'))

    def test_refresh_asks_again_even_when_switched_off(self):
        settings.save_settings({'auto_lookup': False})
        lookups.attach_cached([missing()])
        key = lookups.refresh(missing())
        lookups._worker.wait_idle(5)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(lookups.results_for([key])[key]['state'], 'not_found')

    def test_untrusted_request_fields_are_sanitised(self):
        request = lookups._request_for(missing('../../x.safetensors', source_url='javascript:alert(1)',
                                               source_hash='nothex', source_hash_type='sha256'))
        self.assertEqual(request['filename'], 'x.safetensors')
        self.assertEqual(request['workflow_url'], '')
        self.assertEqual(request['sha256'], '')
