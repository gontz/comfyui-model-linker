"""Filling empty Lora Manager info: which models, in which order, and what is kept."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

try:
    import requests  # noqa: F401
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import hf_identify, lm_api, lm_backfill, overrides
from support import LibraryTestCase

SHA = 'ab' * 32


def sidecar(**fields):
    base = {'file_name': 'm', 'model_name': 'm', 'file_path': '', 'size': 1, 'modified': 1.0,
            'sha256': SHA, 'base_model': 'Unknown', 'preview_url': '', 'civitai': {},
            'modelDescription': '', 'tags': [], 'hash_status': 'completed'}
    base.update(fields)
    return base


class BackfillTestCase(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.data_dir = tempfile.mkdtemp(prefix='model-linker-backfill-')
        self._real = overrides._data_dir
        overrides._data_dir = lambda: self.data_dir
        self.loras = self.add_models('loras', ['m.safetensors'], ['.safetensors'])
        self.model = os.path.join(self.loras, 'm.safetensors')
        self.calls = []
        fake = mock.MagicMock()
        fake.LoraManagerUnavailable = lm_api.LoraManagerUnavailable
        fake.available.return_value = True
        fake.base_models.return_value = ['Flux.1 D']
        for name in ('scan', 'link_huggingface', 'hydrate_huggingface', 'save_metadata', 'enrich_with_ai'):
            getattr(fake, name).side_effect = (lambda n: lambda *a, **k: self.calls.append((n, a, k)))(name)
        for patcher in (mock.patch.object(lm_backfill, 'lm_api', fake),
                        mock.patch.object(lm_backfill, 'PAUSE_BETWEEN_MODELS', 0)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        overrides._data_dir = self._real
        shutil.rmtree(self.data_dir, ignore_errors=True)
        super().tearDown()

    def write_sidecar(self, **fields):
        with open(os.path.join(self.loras, 'm.metadata.json'), 'w', encoding='utf-8') as handle:
            json.dump(sidecar(**fields), handle)

    def names(self):
        return [c[0] for c in self.calls]


class SelectionTests(BackfillTestCase):
    def test_only_empty_sidecars_are_candidates(self):
        self.write_sidecar()
        self.assertEqual(len(lm_backfill.candidates()), 1)
        self.write_sidecar(modelDescription='<p>has info</p>')
        self.assertEqual(lm_backfill.candidates(), [])
        self.write_sidecar(civitai={'id': 5})
        self.assertEqual(lm_backfill.candidates(), [])

    def test_linked_but_empty_is_a_candidate(self):
        self.write_sidecar(source_url='https://huggingface.co/o/r')
        self.assertEqual(len(lm_backfill.candidates()), 1)

    def test_models_opted_out_in_lora_manager_are_left_alone(self):
        for flag in ('exclude', 'skip_metadata_refresh'):
            self.write_sidecar(**{flag: True})
            self.assertEqual(lm_backfill.candidates(), [], flag)


class RunTests(BackfillTestCase):
    def test_linked_model_is_hydrated_without_searching(self):
        self.write_sidecar(source_url='https://huggingface.co/o/r')
        with mock.patch.object(hf_identify, 'find_repo', side_effect=AssertionError('no search')), \
             mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=False)
        self.assertEqual(self.names().count('hydrate_huggingface'), 1)
        self.assertNotIn('link_huggingface', self.names())
        self.assertEqual(lm_backfill.status()['counts']['hydrated'], 1)

    def test_identified_model_is_linked_hydrated_and_labelled(self):
        self.write_sidecar()
        card = {'cardData': {'base_model': 'black-forest-labs/FLUX.1-dev', 'tags': ['style']}}
        with mock.patch.object(hf_identify, 'find_repo',
                               return_value={'repo': 'o/r', 'card': card, 'how': 'hash-verified search'}):
            lm_backfill._run(use_ai=False)
        self.assertIn('scan', self.names())  # one per Lora Manager model type, before anything else
        self.assertEqual([n for n in self.names() if n != 'scan'][:2], ['link_huggingface', 'hydrate_huggingface'])
        saved = [c for c in self.calls if c[0] == 'save_metadata'][-1][1][2]
        self.assertEqual(saved['base_model'], 'Flux.1 D')
        self.assertEqual(saved['tags'], ['style'])

    def test_not_found_is_reported_and_nothing_written(self):
        self.write_sidecar()
        with mock.patch.object(hf_identify, 'find_repo', return_value=None):
            lm_backfill._run(use_ai=False)
        self.assertEqual(lm_backfill.status()['counts']['not_found'], 1)
        self.assertNotIn('link_huggingface', self.names())
        with open(os.path.join(self.data_dir, 'lm-backfill.json'), encoding='utf-8') as handle:
            self.assertEqual(json.load(handle)['results'][0]['outcome'], 'not_found')

    def test_trigger_words_survive_hydration(self):
        self.write_sidecar(source_url='https://huggingface.co/o/r', civitai={'trainedWords': ['STORYBOARD8']})

        def hydrate(*args, **kwargs):
            self.calls.append(('hydrate_huggingface', args, kwargs))
            self.write_sidecar(source_url='https://huggingface.co/o/r', modelDescription='<p>x</p>',
                               civitai={'trainedWords': ['readme_word']})

        lm_backfill.lm_api.hydrate_huggingface.side_effect = hydrate
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=False)
        saved = [c for c in self.calls if c[0] == 'save_metadata'][-1][1][2]
        self.assertEqual(saved['civitai']['trainedWords'], ['STORYBOARD8', 'readme_word'])

    def test_missing_hash_is_computed_and_recorded(self):
        self.write_sidecar(sha256='', hash_status='pending', source_url='https://huggingface.co/o/r')
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=False)
        first_save = next(c for c in self.calls if c[0] == 'save_metadata')[1][2]
        self.assertEqual(first_save['hash_status'], 'completed')
        self.assertEqual(len(first_save['sha256']), 64)

    def test_ai_enrichment_is_opt_in_and_batched(self):
        self.write_sidecar(source_url='https://huggingface.co/o/r')
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=False)
            self.assertNotIn('enrich_with_ai', self.names())
            self.write_sidecar(source_url='https://huggingface.co/o/r')
            lm_backfill._run(use_ai=True)
        self.assertEqual(self.names().count('enrich_with_ai'), 1)

    def test_ai_failure_is_reported_without_failing_the_run(self):
        self.write_sidecar(source_url='https://huggingface.co/o/r')
        lm_backfill.lm_api.enrich_with_ai.side_effect = lm_api.LoraManagerUnavailable(
            'LLM provider is not configured')
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=True)
        state = lm_backfill.status()
        self.assertEqual(state['status'], 'done')
        self.assertEqual(state['counts']['hydrated'], 1)
        self.assertIn('not configured', state['ai_error'])

    def test_limit_runs_a_sample(self):
        self.add_models('loras', ['n.safetensors'], ['.safetensors'])
        self.write_sidecar()
        with open(os.path.join(self.loras, 'n.metadata.json'), 'w', encoding='utf-8') as handle:
            json.dump(sidecar(file_name='n'), handle)
        with mock.patch.object(hf_identify, 'find_repo', return_value=None):
            lm_backfill._run(use_ai=False, limit=1)
        state = lm_backfill.status()
        self.assertEqual((state['total'], state['candidates_total'], state['done']), (1, 2, 1))

    def test_card_cache_lasts_one_run(self):
        hf_identify._cards['stale/repo'] = {'cardData': {}}
        self.write_sidecar()
        with mock.patch.object(hf_identify, 'find_repo', return_value=None):
            lm_backfill._run(use_ai=False)
        self.assertNotIn('stale/repo', hf_identify._cards)

    def test_lora_manager_not_running(self):
        lm_backfill.lm_api.available.return_value = False
        lm_backfill._run(use_ai=False)
        self.assertEqual(lm_backfill.status()['status'], 'error')


class LmApiTests(unittest.TestCase):
    def test_base_models_reads_lora_managers_envelope(self):
        answer = {'success': True, 'data': {'models': ['Flux.1 D', 'SDXL 1.0'], 'source': 'cache'}}
        with mock.patch.object(lm_api, '_call', return_value=answer):
            self.assertEqual(lm_api.base_models(), ['Flux.1 D', 'SDXL 1.0'])

    def test_hydrating_a_model_outside_its_root_is_refused(self):
        root = tempfile.mkdtemp(prefix='model-linker-root-')
        self.addCleanup(shutil.rmtree, root, True)
        outside = os.path.join(os.path.dirname(root), 'elsewhere', 'm.safetensors')
        with mock.patch.object(lm_api, '_call', side_effect=AssertionError('must not be called')):
            with self.assertRaises(lm_api.LoraManagerUnavailable):
                lm_api.hydrate_huggingface(outside, root, 'o/r')
        # Another drive makes os.path.relpath raise ValueError on Windows
        with mock.patch.object(lm_api.os.path, 'relpath', side_effect=ValueError('different drives')), \
             mock.patch.object(lm_api, '_call', side_effect=AssertionError('must not be called')):
            with self.assertRaises(lm_api.LoraManagerUnavailable):
                lm_api.hydrate_huggingface('D:/m.safetensors', root, 'o/r')

    def test_scan_falls_back_to_a_full_scan(self):
        calls = []

        def call(method, route, **kwargs):
            calls.append(kwargs.get('params'))
            if kwargs.get('params'):
                raise lm_api.LoraManagerUnavailable('Unknown model root(s)')
            return {}

        with mock.patch.object(lm_api, '_call', call):
            lm_api.scan('loras', root='/models/loras')
        self.assertEqual(calls, [{'roots': lm_api.lm_path('/models/loras')}, None])
