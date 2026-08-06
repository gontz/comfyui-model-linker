"""The inventory of models a workflow uses that were found on disk."""

import unittest

from core.linker import analyze_and_find_matches, summarize_present_models

from support import LibraryTestCase


class SummaryTests(unittest.TestCase):
    def test_only_models_that_exist_are_reported(self):
        refs = [{'original_path': 'here.safetensors', 'exists': True, 'category': 'loras'},
                {'original_path': 'gone.safetensors', 'exists': False, 'category': 'loras'}]
        self.assertEqual([m['original_path'] for m in summarize_present_models(refs)],
                         ['here.safetensors'])

    def test_a_reference_with_no_exists_flag_is_not_reported(self):
        self.assertEqual(summarize_present_models([{'original_path': 'x.safetensors'}]), [])

    def test_the_canonical_category_is_supplied_for_grouping(self):
        refs = [{'original_path': 'x.safetensors', 'exists': True, 'category': 'clip'}]
        entry = summarize_present_models(refs)[0]
        self.assertEqual(entry['category'], 'clip')
        self.assertEqual(entry['canonical_category'], 'text_encoders')

    def test_bulky_internal_fields_are_not_sent(self):
        refs = [{'original_path': 'x.safetensors', 'exists': True, 'category': 'loras',
                 'full_path': '/very/long/absolute/path/x.safetensors',
                 'source_url': 'https://example.invalid/x'}]
        entry = summarize_present_models(refs)[0]
        self.assertNotIn('full_path', entry)
        self.assertNotIn('exists', entry)

    def test_enough_is_kept_to_locate_the_model_again(self):
        # The folder button reveals by (category, original_path)
        refs = [{'original_path': 'sub/x.safetensors', 'exists': True, 'category': 'loras',
                 'node_id': 4, 'node_type': 'LoraLoader', 'widget_index': 0,
                 'subgraph_id': 'sub-1', 'subgraph_name': 'Inner', 'is_top_level': False}]
        entry = summarize_present_models(refs)[0]
        for field in ('category', 'original_path', 'node_id', 'node_type',
                      'subgraph_name', 'is_top_level'):
            self.assertIn(field, entry)


class AnalyzeIncludesInventoryTests(LibraryTestCase):
    def workflow(self):
        return {'nodes': [
            {'id': 1, 'type': 'CheckpointLoaderSimple', 'widgets_values': ['present.safetensors']},
            {'id': 2, 'type': 'LoraLoader', 'widgets_values': ['absent.safetensors', 1.0, 1.0]},
        ]}

    def test_present_and_missing_are_reported_side_by_side(self):
        self.add_models('checkpoints', ['present.safetensors'])
        result = analyze_and_find_matches(self.workflow())

        self.assertEqual(result['total_missing'], 1)
        self.assertEqual([m['original_path'] for m in result['present_models']],
                         ['present.safetensors'])

    def test_a_workflow_with_nothing_missing_still_reports_its_models(self):
        # The case where the missing list says nothing at all
        self.add_models('checkpoints', ['present.safetensors'])
        result = analyze_and_find_matches(
            {'nodes': [{'id': 1, 'type': 'CheckpointLoaderSimple',
                        'widgets_values': ['present.safetensors']}]})
        self.assertEqual(result['total_missing'], 0)
        self.assertEqual(len(result['present_models']), 1)

    def test_the_two_lists_never_overlap(self):
        self.add_models('checkpoints', ['present.safetensors'])
        result = analyze_and_find_matches(self.workflow())
        present = {m['original_path'] for m in result['present_models']}
        missing = {m['original_path'] for m in result['missing_models']}
        self.assertEqual(present & missing, set())

    def test_they_account_for_every_reference_analysed(self):
        self.add_models('checkpoints', ['present.safetensors'])
        result = analyze_and_find_matches(self.workflow())
        self.assertEqual(len(result['present_models']) + result['total_missing'],
                         result['total_models_analyzed'])

    def test_models_inside_subgraphs_are_included(self):
        self.add_models('loras', ['inner.safetensors'])
        result = analyze_and_find_matches({
            'nodes': [],
            'definitions': {'subgraphs': [{
                'id': 'sub-1', 'name': 'Inner',
                'nodes': [{'id': 5, 'type': 'LoraLoader',
                           'widgets_values': ['inner.safetensors', 1.0, 1.0]}],
            }]},
        })
        entry = result['present_models'][0]
        self.assertEqual(entry['original_path'], 'inner.safetensors')
        self.assertEqual(entry['subgraph_name'], 'Inner')


if __name__ == '__main__':
    unittest.main()
