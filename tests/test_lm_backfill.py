"""Filling empty Lora Manager info: which models, in which order, and what is kept."""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

try:
    import requests  # noqa: F401
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import hf_identify, lm_api, lm_backfill, overrides, routes_downloads
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
        for name in ('scan', 'link_huggingface', 'save_metadata', 'enrich_with_ai'):
            getattr(fake, name).side_effect = (lambda n: lambda *a, **k: self.calls.append((n, a, k)))(name)
        fake.hydrate_huggingface.side_effect = self._default_hydrate
        for patcher in (mock.patch.object(lm_backfill, 'lm_api', fake),
                        mock.patch.object(lm_backfill, 'PAUSE_BETWEEN_MODELS', 0)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        overrides._data_dir = self._real
        shutil.rmtree(self.data_dir, ignore_errors=True)
        lm_backfill._cancel.clear()
        super().tearDown()

    def write_sidecar(self, filename='m', **fields):
        fields.setdefault('file_name', filename)
        fields.setdefault('model_name', filename)
        with open(os.path.join(self.loras, f'{filename}.metadata.json'), 'w', encoding='utf-8') as handle:
            json.dump(sidecar(**fields), handle)

    def names(self):
        return [c[0] for c in self.calls]

    def _default_hydrate(self, path, model_root, repo):
        """
        What a real, successful Lora Manager hydration leaves behind, by
        default: the model-card fields set, and the README's own (here,
        empty) trigger words in place of whatever was there before. A test
        that needs a different outcome (no card written, or a README with
        its own trigger words) overrides `hydrate_huggingface.side_effect`.
        """
        self.calls.append(('hydrate_huggingface', (path, model_root, repo), {}))
        sidecar_file = lm_backfill.sidecar_path(path)
        with open(sidecar_file, encoding='utf-8') as handle:
            current = json.load(handle)
        current['metadata_source'] = 'source:huggingface'
        current['modelDescription'] = '<p>readme</p>'
        current['civitai'] = dict(current.get('civitai') or {})
        current['civitai']['trainedWords'] = []
        with open(sidecar_file, 'w', encoding='utf-8') as handle:
            json.dump(current, handle)

    def _wait_until_done(self, timeout=5):
        """
        Wait for the real background thread `start()` spawned to fully
        terminate - not just for `status()` to stop saying 'running'.

        `_run`'s own status flips to 'done'/'error'/'cancelled' *inside* its
        `try` block, before the `finally` block's own `_save_report()` call
        runs. Polling `status()` alone can return the instant that flip
        happens, while the thread is still mid-flight toward that last save
        - and if the caller's tearDown then restores the real
        `overrides._data_dir` before that save lands, it would write to the
        user's real `data/lm-backfill.json`. Joining the thread itself
        waits for the whole function, finally block included, to finish.
        """
        for thread in threading.enumerate():
            if thread.name == 'model-linker-lm-backfill':
                thread.join(timeout)
        if lm_backfill.status().get('status') == 'running':
            self.fail('backfill did not finish in time')


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

    def test_a_link_to_another_site_is_left_untouched(self):
        """
        A model already linked to ModelScope (or anywhere else that isn't
        HuggingFace) was linked there on purpose. hf_identify can't confirm
        or replace that link, so it must never be searched, relinked, or
        hydrated from here - fix round 1, item 1.
        """
        self.write_sidecar(source_url='https://modelscope.cn/models/o/r')
        before_path = os.path.join(self.loras, 'm.metadata.json')
        with open(before_path, encoding='utf-8') as handle:
            before = handle.read()
        with mock.patch.object(hf_identify, 'find_repo', side_effect=AssertionError('no search')):
            lm_backfill._run(use_ai=False)
        self.assertEqual([n for n in self.names() if n != 'scan'], [])  # no link/hydrate/save_metadata
        state = lm_backfill.status()
        self.assertEqual(state['counts']['linked_elsewhere'], 1)
        self.assertEqual(state['results'][0]['outcome'], 'linked_elsewhere')
        with open(before_path, encoding='utf-8') as handle:
            after = handle.read()
        self.assertEqual(before, after)  # the sidecar itself is untouched

    def test_hydration_that_writes_nothing_is_reported_as_no_card(self):
        """
        Lora Manager's hydration route can answer success while writing
        nothing (HuggingFace unreachable, no README, a gated card, a
        repo/filename mismatch) - fix round 1, item 2.
        """
        self.write_sidecar(source_url='https://huggingface.co/o/r')
        lm_backfill.lm_api.hydrate_huggingface.side_effect = \
            lambda *a, **k: self.calls.append(('hydrate_huggingface', a, k))  # writes nothing
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=False)
        state = lm_backfill.status()
        self.assertEqual(state['counts']['no_card'], 1)
        self.assertEqual(state['results'][0]['outcome'], 'no_card')

    def test_a_model_with_trigger_words_is_not_sent_to_ai(self):
        """
        Lora Manager's AI enrichment replaces trainedWords/notes/usage_tips/
        base_model wholesale, which would undo the trigger-word merge for a
        model that already had its own words - fix round 1, item 3.
        """
        self.write_sidecar(source_url='https://huggingface.co/o/r', civitai={'trainedWords': ['X']})
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=True)
        self.assertNotIn('enrich_with_ai', self.names())
        state = lm_backfill.status()
        self.assertEqual(state['counts'].get('ai_skipped'), 1)
        self.assertTrue(state['results'][0].get('ai_skipped'))
        # the merge-back itself still happens regardless of AI eligibility
        saved = [c for c in self.calls if c[0] == 'save_metadata'][-1][1][2]
        self.assertEqual(saved['civitai']['trainedWords'], ['X'])

    def test_a_model_with_notes_or_usage_tips_is_not_sent_to_ai(self):
        self.write_sidecar(source_url='https://huggingface.co/o/r', usage_tips='{"a": 1}')
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=True)
        self.assertNotIn('enrich_with_ai', self.names())
        self.assertEqual(lm_backfill.status()['counts'].get('ai_skipped'), 1)

    def test_a_set_base_model_is_not_sent_to_ai(self):
        """
        Lora Manager's AI path can overwrite base_model for an
        already-linked model too (e.g. an HF-linked empty LoRA with
        base_model 'LTXV2'), so ai_eligible must check it exactly like
        trigger words/notes/usage_tips - fix round 2, item 2.
        """
        self.write_sidecar(source_url='https://huggingface.co/o/r', base_model='LTXV2')
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=True)
        self.assertNotIn('enrich_with_ai', self.names())
        state = lm_backfill.status()
        self.assertEqual(state['counts'].get('ai_skipped'), 1)
        self.assertTrue(state['results'][0].get('ai_skipped'))

    def test_the_fully_blank_case_is_ai_eligible(self):
        """The positive case for fix round 2, item 2: no trigger words, a
        blank notes/usage_tips, and an unset base model - every gate open."""
        self.write_sidecar(source_url='https://huggingface.co/o/r', notes='', usage_tips='{}', base_model='Unknown')
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=True)
        self.assertIn('enrich_with_ai', self.names())
        self.assertIsNone(lm_backfill.status()['results'][0].get('ai_skipped'))

    def test_a_no_card_model_never_reaches_the_ai_batch(self):
        """Fix round 2, item 2: a model nothing was actually hydrated for
        must never be handed to AI enrichment, use_ai=True or not."""
        self.write_sidecar(source_url='https://huggingface.co/o/r')
        lm_backfill.lm_api.hydrate_huggingface.side_effect = \
            lambda *a, **k: self.calls.append(('hydrate_huggingface', a, k))  # writes nothing
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=True)
        self.assertNotIn('enrich_with_ai', self.names())
        self.assertEqual(lm_backfill.status()['counts']['no_card'], 1)

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

    def test_a_model_that_changed_before_its_turn_is_skipped(self):
        """
        _fill re-reads the sidecar fresh, rather than trusting the snapshot
        candidates() took, and re-checks exclude/skip_metadata_refresh/
        is_empty against that fresh read - fix round 1, item 7.
        """
        self.write_sidecar()
        item = lm_backfill.candidates()[0]
        self.write_sidecar(exclude=True)  # changed after candidates() ran, before _fill's turn
        with mock.patch.object(hf_identify, 'find_repo', side_effect=AssertionError('must not search')):
            outcome = lm_backfill._fill(item, [])
        self.assertEqual(outcome, 'skipped')

    def test_status_preserves_a_failed_runs_outcome_after_restart(self):
        """
        Once idle, status() must hand back the last persisted report's own
        status (e.g. 'error'), not silently relabel it 'idle' - fix round 1,
        item 5.
        """
        self.write_sidecar(source_url='https://huggingface.co/o/r')
        lm_backfill.lm_api.available.return_value = False
        lm_backfill._run(use_ai=False)
        self.assertEqual(lm_backfill._state['status'], 'error')
        lm_backfill._state.clear()
        lm_backfill._state['status'] = 'idle'
        state = lm_backfill.status()
        self.assertEqual(state['status'], 'error')
        self.assertTrue(state['from_report'])

    def test_report_is_saved_after_each_model_not_only_at_the_end(self):
        """Fix round 1, item 6: an interrupted run still leaves a report
        listing every sidecar actually touched so far."""
        self.add_models('loras', ['n.safetensors'], ['.safetensors'])
        self.write_sidecar()
        self.write_sidecar('n')
        seen_after_first = []

        def find_repo(*args, **kwargs):
            try:
                with open(os.path.join(self.data_dir, 'lm-backfill.json'), encoding='utf-8') as handle:
                    seen_after_first.append(len(json.load(handle)['results']))
            except FileNotFoundError:
                seen_after_first.append(0)  # nothing saved to disk yet
            return None

        with mock.patch.object(hf_identify, 'find_repo', side_effect=find_repo):
            lm_backfill._run(use_ai=False)
        # By the time the second model is being identified, the report
        # already reflects the first model's own result.
        self.assertEqual(seen_after_first, [0, 1])

    def test_status_shows_a_restored_running_report_as_interrupted(self):
        """
        A process restart loses the whole in-memory job; the persisted
        report can be caught mid-run (status still 'running'), which must
        not come back looking like a job that's still working - fix round
        2, item 1.
        """
        self.write_sidecar(source_url='https://huggingface.co/o/r')
        lm_backfill._update(status='running', started_at=time.time(), finished_at=None, total=3,
                             candidates_total=3, limit=None, done=1, current='m.safetensors',
                             counts={'hydrated': 1}, results=[{'file': 'm.safetensors', 'outcome': 'hydrated'}],
                             use_ai=False, ai_error=None, error=None)
        lm_backfill._save_report()
        lm_backfill._state.clear()
        lm_backfill._state['status'] = 'idle'
        state = lm_backfill.status()
        self.assertEqual(state['status'], 'interrupted')
        self.assertTrue(state['from_report'])
        self.assertEqual(state['done'], 1)
        self.assertIsNone(state['finished_at'])

    def test_a_report_save_failure_does_not_abort_the_run(self):
        """
        A transient OSError on the per-model save (a Windows antivirus or
        indexer lock on os.replace) must not abort the run - fix round 2,
        item 3. The final save (in `_run`'s `finally`) still runs.
        """
        self.write_sidecar(source_url='https://huggingface.co/o/r')
        real_replace = lm_backfill.os.replace
        calls = {'n': 0}

        def flaky_replace(src, dst):
            calls['n'] += 1
            if calls['n'] == 1:
                raise OSError('locked by antivirus')
            return real_replace(src, dst)

        with mock.patch.object(hf_identify, 'card', return_value=None), \
             mock.patch.object(lm_backfill.os, 'replace', side_effect=flaky_replace):
            lm_backfill._run(use_ai=False)
        self.assertGreaterEqual(calls['n'], 2)  # the per-model save failed, the final save still ran
        self.assertEqual(lm_backfill.status()['status'], 'done')

    def test_trigger_words_before_are_recorded_when_the_merge_save_fails(self):
        """
        If hydration clears trainedWords and the merge-back save then
        fails, the words would otherwise be lost entirely (the outcome is
        just 'failed', with nothing to restore from) - fix round 2, item 5.
        """
        self.write_sidecar(source_url='https://huggingface.co/o/r', civitai={'trainedWords': ['STORYBOARD8']})

        def hydrate(*args, **kwargs):
            self.calls.append(('hydrate_huggingface', args, kwargs))
            self.write_sidecar(source_url='https://huggingface.co/o/r', modelDescription='<p>x</p>',
                               civitai={'trainedWords': []})  # the README named none

        lm_backfill.lm_api.hydrate_huggingface.side_effect = hydrate
        lm_backfill.lm_api.save_metadata.side_effect = lm_api.LoraManagerUnavailable('disk full')
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=False)
        state = lm_backfill.status()
        self.assertEqual(state['results'][0]['outcome'], 'failed')
        self.assertEqual(state['results'][0]['trainedWords_before'], ['STORYBOARD8'])

    def test_an_already_hf_sourced_but_still_undescribed_sidecar_is_not_falsely_hydrated(self):
        """
        is_empty() only looks at civitai.id/modelDescription, so a sidecar
        can already carry metadata_source: 'source:huggingface' from an
        earlier partial state and still be a candidate. If this run's
        hydration genuinely writes nothing new, comparing only the
        post-hydration value would wrongly call it 'hydrated' - fix round
        2, item 6.
        """
        self.write_sidecar(source_url='https://huggingface.co/o/r', metadata_source='source:huggingface')
        lm_backfill.lm_api.hydrate_huggingface.side_effect = \
            lambda *a, **k: self.calls.append(('hydrate_huggingface', a, k))  # writes nothing new
        with mock.patch.object(hf_identify, 'card', return_value=None):
            lm_backfill._run(use_ai=False)
        self.assertEqual(lm_backfill.status()['counts']['no_card'], 1)


class StartStopTests(BackfillTestCase):
    def test_start_refuses_a_second_concurrent_run(self):
        """
        `_state['status']` is set to 'running' synchronously inside the
        first `start()` call, before the worker thread is even spawned, so
        the second call's refusal is never itself racy. What *is* racy
        without care is the whole premise of the test: the worker (fully
        mocked, PAUSE_BETWEEN_MODELS=0) can finish the entire job before
        this thread's next line even runs, especially around the real
        time.sleep(0) call, which is a known thread-switch point - and then
        the "already running" being asserted wouldn't be testing anything.
        An Event blocks the worker inside `_fill` (via `hf_identify.card`,
        which this model's path reaches) so it is *guaranteed* to still be
        running when the second `start()` call happens - fix round 2, item 4.
        """
        self.write_sidecar(source_url='https://huggingface.co/o/r')
        release = threading.Event()

        def blocking_card(*args, **kwargs):
            release.wait(5)
            return None

        with mock.patch.object(hf_identify, 'card', side_effect=blocking_card):
            self.assertTrue(lm_backfill.start(use_ai=False))
            self.assertFalse(lm_backfill.start(use_ai=False))  # already running, guaranteed by the block above
            release.set()
            self._wait_until_done()
        self.assertEqual(lm_backfill.status()['status'], 'done')
        self.assertNotIn('model-linker-lm-backfill', [t.name for t in threading.enumerate()])

    def test_cancel_stops_before_the_next_model(self):
        self.write_sidecar()
        self.add_models('loras', ['n.safetensors'], ['.safetensors'])
        self.write_sidecar('n')

        def cancel_after_first(*args, **kwargs):
            lm_backfill.cancel()
            return None

        with mock.patch.object(hf_identify, 'find_repo', side_effect=cancel_after_first):
            lm_backfill._run(use_ai=False)
        state = lm_backfill.status()
        self.assertEqual(state['status'], 'cancelled')
        self.assertEqual(state['done'], 1)
        self.assertLess(state['done'], state['total'])


class RouteValidationTests(unittest.TestCase):
    """`limit` validation shared by the start route (fix round 1, item 16)."""

    def test_invalid_values_are_rejected(self):
        for limit in (True, False, 0, -1, 'five', 1.5):
            self.assertTrue(routes_downloads._invalid_limit(limit), limit)

    def test_valid_values_are_accepted(self):
        for limit in (None, 1, 100):
            self.assertFalse(routes_downloads._invalid_limit(limit), limit)


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

    def test_hydrating_a_zero_byte_file_is_refused(self):
        """
        Lora Manager only treats a same-named file as already present when
        it has real bytes; a zero-byte file would make it download the
        repo's copy over this path instead - fix round 1, item 4.
        """
        root = tempfile.mkdtemp(prefix='model-linker-root-')
        self.addCleanup(shutil.rmtree, root, True)
        empty = os.path.join(root, 'm.safetensors')
        open(empty, 'wb').close()
        with mock.patch.object(lm_api, '_call', side_effect=AssertionError('must not be called')):
            with self.assertRaises(lm_api.LoraManagerUnavailable):
                lm_api.hydrate_huggingface(empty, root, 'o/r')

    def test_scan_falls_back_to_a_full_scan_on_an_unknown_root(self):
        calls = []

        def call(method, route, **kwargs):
            calls.append(kwargs.get('params'))
            if kwargs.get('params'):
                raise lm_api.LoraManagerUnavailable('Unknown model root(s)', status_code=400)
            return {}

        with mock.patch.object(lm_api, '_call', call):
            lm_api.scan('loras', root='/models/loras')
        self.assertEqual(calls, [{'roots': lm_api.lm_path('/models/loras')}, None])

    def test_scan_does_not_fall_back_on_a_connection_failure(self):
        """A timeout or connection error (no status_code at all) means Lora
        Manager isn't reachable right now - scanning "everything" would just
        fail the same way again, so this must propagate - fix round 1, item 12."""
        calls = []

        def call(method, route, **kwargs):
            calls.append(kwargs.get('params'))
            raise lm_api.LoraManagerUnavailable('timed out')

        with mock.patch.object(lm_api, '_call', call):
            with self.assertRaises(lm_api.LoraManagerUnavailable):
                lm_api.scan('loras', root='/models/loras')
        self.assertEqual(calls, [{'roots': lm_api.lm_path('/models/loras')}])
