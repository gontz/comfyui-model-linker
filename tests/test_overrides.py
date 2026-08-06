"""
Persisted user selections.

Every test redirects the data directory into its own temporary folder - the
real one holds the developer's saved selections, and a test that wrote there
would quietly destroy them.
"""

import json
import os
import unittest

from core import overrides
from core.overrides import (
    clear_overrides,
    delete_override,
    find_override_model,
    find_override_path,
    get_overrides_path,
    load_overrides,
    record_override,
    record_overrides,
    replace_overrides,
)

from support import LibraryTestCase


class OverridesTestCase(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.data_dir = os.path.join(self.root, 'data')
        os.makedirs(self.data_dir, exist_ok=True)
        self._real_data_dir = overrides._data_dir
        overrides._data_dir = lambda: self.data_dir

    def tearDown(self):
        overrides._data_dir = self._real_data_dir
        super().tearDown()

    def stored(self):
        with open(get_overrides_path(), encoding='utf-8') as handle:
            return json.load(handle)


class RecordAndFindTests(OverridesTestCase):
    def test_a_selection_is_found_again(self):
        record_override('missing.safetensors', 'checkpoints',
                        resolved_path=r'C:\models\checkpoints\replacement.safetensors')
        self.assertEqual(find_override_path('missing.safetensors', 'checkpoints'),
                         r'C:\models\checkpoints\replacement.safetensors')

    def test_a_selection_is_found_by_a_differently_spelled_name(self):
        # Keys are built from the normalised filename, so the same model
        # written with hyphens instead of underscores still matches
        record_override('flux1_dev.safetensors', 'checkpoints', resolved_path='/m/new.safetensors')
        self.assertEqual(find_override_path('flux1-dev.safetensors', 'checkpoints'),
                         '/m/new.safetensors')

    def test_keys_are_category_scoped(self):
        record_override('shared_name.safetensors', 'loras', resolved_path='/m/lora.safetensors')
        record_override('shared_name.safetensors', 'vae', resolved_path='/m/vae.safetensors')
        self.assertEqual(find_override_path('shared_name.safetensors', 'loras'),
                         '/m/lora.safetensors')
        self.assertEqual(find_override_path('shared_name.safetensors', 'vae'),
                         '/m/vae.safetensors')
        self.assertEqual(len(self.stored()['mappings']), 2)

    def test_a_selection_saved_under_another_category_is_still_offered(self):
        record_override('flux1_dev.safetensors', 'checkpoints', resolved_path='/m/new.safetensors')
        self.assertEqual(find_override_path('flux1_dev.safetensors', 'diffusion_models'),
                         '/m/new.safetensors')

    def test_an_unknown_category_is_normalised_to_any(self):
        record_override('a.safetensors', 'unknown', resolved_path='/m/new.safetensors')
        self.assertEqual(self.stored()['mappings'][0]['key'], 'any:a')

    def test_choosing_again_replaces_the_earlier_choice(self):
        record_override('a.safetensors', 'loras', resolved_path='/m/first.safetensors')
        record_override('a.safetensors', 'loras', resolved_path='/m/second.safetensors')
        mappings = self.stored()['mappings']
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0]['path'], '/m/second.safetensors')

    def test_a_resolved_model_contributes_its_metadata(self):
        record_override('a.safetensors', 'loras',
                        resolved={'path': '/m/new.safetensors', 'filename': 'new.safetensors',
                                  'relative_path': 'sub/new.safetensors',
                                  'base_directory': '/m'})
        entry = self.stored()['mappings'][0]
        self.assertEqual(entry['filename'], 'new.safetensors')
        self.assertEqual(entry['relative_path'], 'sub/new.safetensors')

    def test_a_selection_with_nothing_to_record_is_ignored(self):
        self.assertFalse(record_override('a.safetensors', 'loras'))
        self.assertFalse(record_override('', 'loras', resolved_path='/m/new.safetensors'))
        self.assertEqual(load_overrides()['mappings'], [])

    def test_nothing_is_found_when_nothing_was_saved(self):
        self.assertIsNone(find_override_path('a.safetensors', 'loras'))


class BatchRecordingTests(OverridesTestCase):
    def test_a_whole_resolution_is_saved_in_one_write(self):
        written = record_overrides([
            {'original_path': 'a.safetensors', 'category': 'loras',
             'resolved_path': '/m/a_new.safetensors'},
            {'original_path': 'b.safetensors', 'category': 'vae',
             'resolved_path': '/m/b_new.safetensors'},
        ])
        self.assertEqual(written, 2)
        self.assertEqual(len(self.stored()['mappings']), 2)

    def test_unusable_selections_are_skipped_rather_than_failing_the_batch(self):
        written = record_overrides([
            {'original_path': 'a.safetensors', 'category': 'loras',
             'resolved_path': '/m/a_new.safetensors'},
            {'original_path': '', 'category': 'loras', 'resolved_path': '/m/x.safetensors'},
        ])
        self.assertEqual(written, 1)

    def test_an_empty_batch_writes_nothing(self):
        self.assertEqual(record_overrides([]), 0)
        self.assertFalse(os.path.exists(get_overrides_path()))


class DurabilityTests(OverridesTestCase):
    def test_saving_leaves_no_temporary_files_behind(self):
        record_override('a.safetensors', 'loras', resolved_path='/m/new.safetensors')
        leftovers = [n for n in os.listdir(self.data_dir) if n != 'overrides.json']
        self.assertEqual(leftovers, [])

    def test_a_corrupt_file_is_treated_as_empty_rather_than_raising(self):
        with open(get_overrides_path(), 'w', encoding='utf-8') as handle:
            handle.write('{ this is not json')
        self.assertEqual(load_overrides()['mappings'], [])

    def test_a_file_of_the_wrong_shape_is_treated_as_empty(self):
        with open(get_overrides_path(), 'w', encoding='utf-8') as handle:
            json.dump(['not', 'a', 'document'], handle)
        self.assertEqual(load_overrides()['mappings'], [])


class MaintenanceTests(OverridesTestCase):
    def setUp(self):
        super().setUp()
        record_override('a.safetensors', 'loras', resolved_path='/m/a.safetensors')
        record_override('b.safetensors', 'vae', resolved_path='/m/b.safetensors')

    def test_a_single_override_can_be_deleted_by_key(self):
        self.assertTrue(delete_override('loras:a'))
        self.assertEqual([m['key'] for m in self.stored()['mappings']], ['vae:b'])

    def test_deleting_something_absent_reports_that_it_did_nothing(self):
        self.assertFalse(delete_override('loras:nonexistent'))
        self.assertFalse(delete_override(''))
        self.assertEqual(len(self.stored()['mappings']), 2)

    def test_everything_can_be_cleared(self):
        clear_overrides()
        self.assertEqual(self.stored()['mappings'], [])

    def test_replacing_the_document_drops_malformed_entries(self):
        self.assertTrue(replace_overrides({'version': 1, 'mappings': [
            {'key': 'loras:kept', 'path': '/m/kept.safetensors'},
            {'key': 'loras:no-path'},
            {'path': '/m/no-key.safetensors'},
            'not even a dict',
        ]}))
        self.assertEqual([m['key'] for m in self.stored()['mappings']], ['loras:kept'])

    def test_a_document_of_the_wrong_shape_is_refused(self):
        self.assertFalse(replace_overrides('nope'))
        self.assertFalse(replace_overrides({'mappings': 'not a list'}))
        self.assertEqual(len(self.stored()['mappings']), 2, 'the old document must survive')


class OverrideModelLookupTests(OverridesTestCase):
    def test_a_saved_choice_is_matched_to_the_catalogued_model(self):
        available = [{'path': r'C:\models\loras\new.safetensors', 'filename': 'new.safetensors'}]
        record_override('old.safetensors', 'loras',
                        resolved_path=r'C:\models\loras\new.safetensors')
        found = find_override_model('old.safetensors', 'loras', available)
        self.assertEqual(found['filename'], 'new.safetensors')

    def test_a_choice_pointing_at_a_file_that_is_gone_is_not_offered(self):
        record_override('old.safetensors', 'loras', resolved_path=r'C:\models\loras\gone.safetensors')
        self.assertIsNone(find_override_model('old.safetensors', 'loras', []))


class OverridesInAnalysisTests(OverridesTestCase):
    def test_a_saved_choice_is_promoted_to_the_top_of_the_suggestions(self):
        from core.linker import analyze_and_find_matches

        base = self.add_models('loras', ['unrelated_name.safetensors',
                                         'grain_v2.safetensors'])
        # Left alone, `grain_v2` is the closer name; the saved choice wins anyway
        record_override('grain.safetensors', 'loras',
                        resolved_path=os.path.join(base, 'unrelated_name.safetensors'))

        result = analyze_and_find_matches(
            {'nodes': [{'id': 1, 'type': 'LoraLoader',
                        'widgets_values': ['grain.safetensors', 1.0, 1.0]}]})

        top = result['missing_models'][0]['matches'][0]
        self.assertEqual(top['filename'], 'unrelated_name.safetensors')
        self.assertEqual(top['confidence'], 100.0)
        self.assertTrue(top['is_override'])

    def test_a_saved_choice_is_not_offered_twice(self):
        from core.linker import analyze_and_find_matches

        base = self.add_models('loras', ['grain_v2.safetensors'])
        record_override('grain.safetensors', 'loras',
                        resolved_path=os.path.join(base, 'grain_v2.safetensors'))

        result = analyze_and_find_matches(
            {'nodes': [{'id': 1, 'type': 'LoraLoader',
                        'widgets_values': ['grain.safetensors', 1.0, 1.0]}]})

        matches = result['missing_models'][0]['matches']
        self.assertEqual([m['filename'] for m in matches], ['grain_v2.safetensors'])
        self.assertTrue(matches[0]['is_override'])


if __name__ == '__main__':
    unittest.main()
