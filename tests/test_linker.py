"""
Physical-file identity, candidate selection, and the picker's catalogue.

Category directories are frequently links to one shared folder, so the same
file is catalogued once per alias. Everything here is about telling "the same
file again" apart from "a different file".
"""

import os
import unittest

from core.linker import (
    analyze_and_find_matches,
    group_models_by_physical_file,
    list_available_models,
    physical_file_key,
    select_candidates,
)
from core.scanner import get_model_files

from support import LibraryTestCase


class PhysicalFileKeyTests(unittest.TestCase):
    def test_the_resolved_path_is_what_identifies_a_file(self):
        left = {'path': r'C:\models\clip\umt5.safetensors',
                'real_path': r'C:\models\text_encoders\umt5.safetensors'}
        right = {'path': r'C:\models\text_encoders\umt5.safetensors',
                 'real_path': r'C:\models\text_encoders\umt5.safetensors'}
        self.assertEqual(physical_file_key(left), physical_file_key(right))

    def test_it_falls_back_to_path_when_no_real_path_was_recorded(self):
        self.assertTrue(physical_file_key({'path': r'C:\models\a.safetensors'}))

    def test_an_entry_with_no_path_at_all_yields_an_empty_key(self):
        self.assertEqual(physical_file_key({}), '')


class CandidateSelectionTests(LibraryTestCase):
    def test_link_aliases_collapse_to_one_candidate(self):
        self.add_models('text_encoders', ['umt5_xxl_fp16.safetensors'])
        self.link_category('clip', 'text_encoders')

        groups = group_models_by_physical_file(get_model_files())
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(select_candidates(groups, 'text_encoders')), 1)

    def test_the_entry_from_the_expected_category_is_the_one_chosen(self):
        # The written path resolves against the chosen category's folder, so
        # picking the wrong alias produces a workflow that still fails to load
        self.add_models('text_encoders', ['umt5_xxl_fp16.safetensors'])
        self.link_category('clip', 'text_encoders')
        groups = group_models_by_physical_file(get_model_files())

        for wanted in ('text_encoders', 'clip'):
            chosen = select_candidates(groups, wanted)[0]
            self.assertEqual(chosen['category'], wanted)

    def test_an_alias_of_the_wanted_category_is_accepted_when_it_is_all_there_is(self):
        self.add_models('clip', ['umt5_xxl_fp16.safetensors'])
        groups = group_models_by_physical_file(get_model_files())
        chosen = select_candidates(groups, 'text_encoders')[0]
        self.assertEqual(chosen['category'], 'clip')

    def test_preferred_entries_come_first(self):
        self.add_models('text_encoders', ['umt5_xxl_fp16.safetensors'])
        self.add_models('loras', ['grain.safetensors'])
        picked = select_candidates(group_models_by_physical_file(get_model_files()),
                                   'text_encoders')
        self.assertEqual(picked[0]['category'], 'text_encoders')

    def test_no_wanted_category_still_yields_one_candidate_per_file(self):
        self.add_models('checkpoints', ['a.safetensors', 'b.safetensors'])
        groups = group_models_by_physical_file(get_model_files())
        self.assertEqual(len(select_candidates(groups, None)), 2)


class AvailableModelsTests(LibraryTestCase):
    def test_aliases_stay_listed_but_share_a_file_id(self):
        # Each row has to keep its category - that is what decides which folder
        # the written path resolves against - so rows are not collapsed; the
        # id is how the picker tells an alias from a genuinely different file.
        self.add_models('text_encoders', ['umt5_xxl_fp16.safetensors'])
        self.link_category('clip', 'text_encoders')

        listing = list_available_models()
        self.assertEqual(len(listing), 2)
        self.assertEqual(len({m['file_id'] for m in listing}), 1)
        self.assertEqual({m['category'] for m in listing}, {'text_encoders', 'clip'})

    def test_different_files_never_share_a_file_id(self):
        self.add_models('checkpoints', ['a.safetensors', 'b.safetensors'])
        listing = list_available_models()
        self.assertEqual(len({m['file_id'] for m in listing}), 2)

    def test_duplicate_directories_within_one_category_are_dropped(self):
        base = self.add_models('checkpoints', ['flux1_dev.safetensors'])
        # The same directory registered twice under one category
        import fake_folder_paths
        fake_folder_paths.add_category('checkpoints', [base, base])
        self.assertEqual(len(list_available_models()), 1)

    def test_internal_fields_are_not_sent_to_the_frontend(self):
        self.add_models('checkpoints', ['flux1_dev.safetensors'])
        for entry in list_available_models():
            self.assertNotIn('real_path', entry)
            self.assertNotIn('_norm', entry)

    def test_the_canonical_category_is_supplied_for_the_pickers_scoping(self):
        self.add_models('clip', ['umt5_xxl_fp16.safetensors'])
        entry = list_available_models()[0]
        self.assertEqual(entry['category'], 'clip')
        self.assertEqual(entry['canonical_category'], 'text_encoders')


class AnalyzeTests(LibraryTestCase):
    def workflow(self, node_type, value):
        return {'nodes': [{'id': 1, 'type': node_type, 'widgets_values': [value]}]}

    def test_a_present_model_is_not_reported_missing(self):
        self.add_models('checkpoints', ['flux1_dev.safetensors'])
        result = analyze_and_find_matches(
            self.workflow('CheckpointLoaderSimple', 'flux1_dev.safetensors'))
        self.assertEqual(result['total_missing'], 0)

    def test_a_missing_model_is_reported_with_ranked_suggestions(self):
        self.add_models('checkpoints', ['flux1_dev_fp8.safetensors', 'unrelated.safetensors'])
        result = analyze_and_find_matches(
            self.workflow('CheckpointLoaderSimple', 'flux1_dev.safetensors'))

        self.assertEqual(result['total_missing'], 1)
        missing = result['missing_models'][0]
        self.assertEqual(missing['original_path'], 'flux1_dev.safetensors')
        self.assertEqual(missing['category'], 'checkpoints')
        self.assertEqual(missing['matches'][0]['filename'], 'flux1_dev_fp8.safetensors')

    def test_suggestions_hold_one_entry_per_physical_file(self):
        self.add_models('text_encoders', ['umt5_xxl_fp16.safetensors'])
        self.link_category('clip', 'text_encoders')
        result = analyze_and_find_matches(
            self.workflow('CLIPLoader', 'umt5_xxl_fp8.safetensors'))

        matches = result['missing_models'][0]['matches']
        self.assertEqual(len(matches), 1, [m['filename'] for m in matches])

    def test_the_canonical_category_reaches_the_frontend(self):
        self.add_models('unet', ['flux1_dev_fp8.safetensors'])
        result = analyze_and_find_matches(
            self.workflow('UNETLoader', 'flux1_dev.safetensors'))
        self.assertEqual(result['missing_models'][0]['canonical_category'], 'diffusion_models')


if __name__ == '__main__':
    unittest.main()
