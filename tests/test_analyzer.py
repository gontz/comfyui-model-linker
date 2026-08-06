"""Reading model references out of a workflow."""

import unittest

from core.workflow_analyzer import (
    analyze_workflow_models,
    get_node_model_info,
    identify_missing_models,
    is_model_filename,
    resolve_model_reference,
    try_resolve_model_path,
)

from support import LibraryTestCase


class IsModelFilenameTests(unittest.TestCase):
    def test_model_extensions_are_recognised(self):
        for name in ('a.safetensors', 'b.CKPT', 'c.gguf', 'd.onnx'):
            self.assertTrue(is_model_filename(name), name)

    def test_other_files_are_not(self):
        for name in ('a.json', 'b.png', 'c.txt', 'notes'):
            self.assertFalse(is_model_filename(name), name)

    def test_non_strings_are_not(self):
        for value in (None, 3, {'lora': 'a.safetensors'}, ['a.safetensors']):
            self.assertFalse(is_model_filename(value), repr(value))


class ResolutionTests(LibraryTestCase):
    def test_a_present_model_resolves_within_its_category(self):
        self.add_models('checkpoints', ['flux1_dev.safetensors'])
        resolved = try_resolve_model_path('flux1_dev.safetensors', ['checkpoints'])
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved[0], 'checkpoints')

    def test_an_absent_model_resolves_to_nothing(self):
        self.add_models('checkpoints', ['flux1_dev.safetensors'])
        self.assertIsNone(try_resolve_model_path('missing.safetensors', ['checkpoints']))

    def test_every_category_is_searched_when_none_is_given(self):
        self.add_models('loras', ['grain.safetensors'])
        resolved = try_resolve_model_path('grain.safetensors')
        self.assertEqual(resolved[0], 'loras')

    def test_an_extension_less_reference_finds_its_file(self):
        # How the Lora Manager pack stores names
        self.add_models('loras', ['grain.safetensors'])
        resolved = resolve_model_reference('grain', 'loras', extension_less=True)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved[0], 'loras')

    def test_an_extension_less_reference_matches_on_the_basename(self):
        base = self.category_dir('loras')
        from support import write_file
        import os
        write_file(os.path.join(base, 'flux', 'grain.safetensors'))
        self.add_models('loras', [], directory=base)
        self.assertIsNotNone(resolve_model_reference('grain', 'loras', extension_less=True))


class NodeExtractionTests(LibraryTestCase):
    def test_a_present_model_is_marked_as_existing(self):
        self.add_models('checkpoints', ['flux1_dev.safetensors'])
        refs = get_node_model_info({'id': 1, 'type': 'CheckpointLoaderSimple',
                                    'widgets_values': ['flux1_dev.safetensors']})
        self.assertEqual(len(refs), 1)
        self.assertTrue(refs[0]['exists'])
        self.assertEqual(refs[0]['category'], 'checkpoints')

    def test_an_absent_model_is_marked_missing_and_keeps_the_hinted_category(self):
        self.add_models('checkpoints', ['other.safetensors'])
        refs = get_node_model_info({'id': 1, 'type': 'CheckpointLoaderSimple',
                                    'widgets_values': ['flux1_dev.safetensors']})
        self.assertFalse(refs[0]['exists'])
        self.assertEqual(refs[0]['category'], 'checkpoints')

    def test_note_nodes_are_never_scanned(self):
        # Their text legitimately contains model filenames
        for node_type in ('MarkdownNote', 'Note', 'Reroute'):
            refs = get_node_model_info({'id': 1, 'type': node_type,
                                        'widgets_values': ['see flux1_dev.safetensors']})
            self.assertEqual(refs, [], node_type)

    def test_non_model_widget_values_are_ignored(self):
        refs = get_node_model_info({'id': 1, 'type': 'CLIPTextEncode',
                                    'widgets_values': ['a photo of a cat', 42, True]})
        self.assertEqual(refs, [])

    def test_dict_shaped_widgets_values_are_read(self):
        # Newer frontends may serialise widgets_values as an object
        self.add_models('checkpoints', ['flux1_dev.safetensors'])
        refs = get_node_model_info({'id': 1, 'type': 'CheckpointLoaderSimple',
                                    'widgets_values': {'ckpt_name': 'flux1_dev.safetensors'}})
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]['widget_index'], 'ckpt_name')

    def test_a_model_nested_in_a_widget_object_is_read(self):
        # rgthree's Power Lora Loader stores objects directly in widget slots
        node = {'id': 9, 'type': 'Power Lora Loader (rgthree)',
                'widgets_values': [{}, {'type': 'PowerLoraLoaderHeaderWidget'},
                                   {'on': True, 'lora': 'grain.safetensors', 'strength': 1}]}
        refs = get_node_model_info(node)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]['nested_key'], 'lora')
        self.assertEqual(refs[0]['widget_index'], 2)
        self.assertIsNone(refs[0].get('list_index'))


class WorkflowMetadataTests(LibraryTestCase):
    def node_with_metadata(self, value, directory, url=None):
        properties = {'models': [{'name': value, 'directory': directory,
                                  **({'url': url} if url else {})}]}
        return {'id': 1, 'type': 'SomeUnknownLoader',
                'widgets_values': [value], 'properties': properties}

    def test_metadata_is_matched_by_widget_value_not_by_input_name(self):
        # `name` in properties.models is the filename, which is what tripped
        # this up before: it reads like an input name but is not one
        refs = get_node_model_info(
            self.node_with_metadata('flux1_dev.safetensors', 'diffusion_models'))
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]['category'], 'diffusion_models')

    def test_metadata_supplies_a_download_url_for_a_model_found_nowhere(self):
        node = self.node_with_metadata('flux1_dev.safetensors', 'diffusion_models',
                                       url='https://example.invalid/flux1_dev.safetensors')
        ref = get_node_model_info(node)[0]
        self.assertFalse(ref['exists'])
        self.assertEqual(ref['source_url'], 'https://example.invalid/flux1_dev.safetensors')

    def test_metadata_finds_a_widget_holding_a_subfolder_path(self):
        node = {'id': 1, 'type': 'SomeUnknownLoader',
                'widgets_values': ['flux/flux1_dev.safetensors'],
                'properties': {'models': [{'name': 'flux1_dev.safetensors',
                                           'directory': 'diffusion_models'}]}}
        self.assertEqual(get_node_model_info(node)[0]['category'], 'diffusion_models')

    def test_a_declared_model_without_a_model_extension_is_still_found(self):
        node = self.node_with_metadata('some_model_without_extension', 'checkpoints')
        self.assertEqual(len(get_node_model_info(node)), 1)


class WorkflowTraversalTests(LibraryTestCase):
    def workflow_with_subgraph(self):
        return {
            'nodes': [
                {'id': 1, 'type': 'CheckpointLoaderSimple',
                 'widgets_values': ['top_level.safetensors']},
            ],
            'definitions': {'subgraphs': [{
                'id': 'sub-uuid-1', 'name': 'My Subgraph',
                'nodes': [{'id': 5, 'type': 'LoraLoader',
                           'widgets_values': ['inside.safetensors', 1.0, 1.0]}],
            }]},
        }

    def test_nodes_inside_subgraph_definitions_are_found(self):
        refs = analyze_workflow_models(self.workflow_with_subgraph())
        by_path = {r['original_path']: r for r in refs}
        self.assertEqual(set(by_path), {'top_level.safetensors', 'inside.safetensors'})

    def test_subgraph_membership_is_recorded_so_the_update_can_find_the_node_again(self):
        refs = analyze_workflow_models(self.workflow_with_subgraph())
        inside = next(r for r in refs if r['original_path'] == 'inside.safetensors')
        self.assertFalse(inside['is_top_level'])
        self.assertEqual(inside['subgraph_id'], 'sub-uuid-1')
        self.assertEqual(inside['subgraph_name'], 'My Subgraph')

        top = next(r for r in refs if r['original_path'] == 'top_level.safetensors')
        self.assertTrue(top['is_top_level'])
        self.assertIsNone(top['subgraph_id'])

    def test_a_node_whose_type_is_a_subgraph_uuid_is_labelled_as_that_subgraph(self):
        workflow = self.workflow_with_subgraph()
        workflow['nodes'].append({'id': 2, 'type': 'sub-uuid-1',
                                  'widgets_values': ['instance.safetensors']})
        refs = analyze_workflow_models(workflow)
        instance = next(r for r in refs if r['original_path'] == 'instance.safetensors')
        self.assertTrue(instance['is_top_level'])
        self.assertEqual(instance['subgraph_id'], 'sub-uuid-1')

    def test_one_bad_node_does_not_abort_the_whole_analysis(self):
        workflow = {'nodes': [
            {'id': 1, 'type': 'CheckpointLoaderSimple', 'widgets_values': 'not-a-list'},
            {'id': 2, 'type': 'CheckpointLoaderSimple', 'widgets_values': ['a.safetensors']},
        ]}
        refs = analyze_workflow_models(workflow)
        self.assertEqual([r['original_path'] for r in refs], ['a.safetensors'])

    def test_only_absent_models_are_reported_missing(self):
        refs = [{'original_path': 'a.safetensors', 'exists': True},
                {'original_path': 'b.safetensors', 'exists': False},
                {'original_path': 'c.safetensors'}]
        missing = identify_missing_models(refs)
        self.assertEqual([r['original_path'] for r in missing],
                         ['b.safetensors', 'c.safetensors'])


if __name__ == '__main__':
    unittest.main()
