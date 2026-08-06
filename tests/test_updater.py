"""Patching model paths back into a workflow."""

import os
import unittest

from core.workflow_updater import (
    convert_to_relative_path,
    get_base_directory_for_model,
    update_model_path,
    update_workflow_nodes,
)

from support import LibraryTestCase, write_file


class RelativePathTests(LibraryTestCase):
    def test_a_path_is_stored_in_the_form_comfyui_lists_it(self):
        base = self.add_models('checkpoints', ['flux1_dev.safetensors'])
        stored = convert_to_relative_path(os.path.join(base, 'flux1_dev.safetensors'),
                                          'checkpoints')
        self.assertEqual(stored, 'flux1_dev.safetensors')

    def test_subfolders_are_kept_with_native_separators(self):
        # ComfyUI's own listing uses OS-native separators, and the stored value
        # has to match it exactly for the workflow to validate
        base = self.category_dir('loras')
        write_file(os.path.join(base, 'flux', 'grain.safetensors'))
        self.add_models('loras', [], directory=base)

        stored = convert_to_relative_path(os.path.join(base, 'flux', 'grain.safetensors'), 'loras')
        self.assertEqual(stored, os.path.join('flux', 'grain.safetensors'))

    def test_an_already_relative_path_is_returned_unchanged(self):
        self.assertEqual(convert_to_relative_path('flux/grain.safetensors', 'loras'),
                         'flux/grain.safetensors')

    def test_an_unlisted_file_falls_back_to_the_given_base_directory(self):
        self.add_models('loras', [])
        elsewhere = os.path.join(self.root, 'elsewhere')
        stored = convert_to_relative_path(os.path.join(elsewhere, 'sub', 'x.safetensors'),
                                          'loras', base_directory=elsewhere)
        self.assertEqual(stored, os.path.join('sub', 'x.safetensors'))

    def test_with_nothing_to_go_on_it_falls_back_to_the_filename(self):
        self.assertEqual(
            convert_to_relative_path(os.path.join(self.root, 'x', 'y.safetensors'), 'loras'),
            'y.safetensors')

    def test_the_base_directory_of_a_model_can_be_recovered(self):
        base = self.add_models('checkpoints', ['flux1_dev.safetensors'])
        model = {'path': os.path.join(base, 'flux1_dev.safetensors')}
        self.assertEqual(get_base_directory_for_model(model, 'checkpoints'), base)


class UpdateModelPathTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.base = self.add_models('checkpoints', ['replacement.safetensors'])
        self.replacement = os.path.join(self.base, 'replacement.safetensors')

    def workflow(self, **node):
        node.setdefault('id', 1)
        node.setdefault('type', 'CheckpointLoaderSimple')
        return {'nodes': [node]}

    def test_a_widget_value_is_replaced(self):
        workflow = self.workflow(widgets_values=['missing.safetensors', 42])
        self.assertTrue(update_model_path(workflow, 1, 0, self.replacement,
                                          category='checkpoints'))
        self.assertEqual(workflow['nodes'][0]['widgets_values'],
                         ['replacement.safetensors', 42])

    def test_an_unknown_node_is_reported_rather_than_guessed_at(self):
        workflow = self.workflow(widgets_values=['missing.safetensors'])
        self.assertFalse(update_model_path(workflow, 999, 0, self.replacement))

    def test_an_out_of_range_widget_index_is_refused(self):
        workflow = self.workflow(widgets_values=['missing.safetensors'])
        self.assertFalse(update_model_path(workflow, 1, 5, self.replacement))

    def test_a_negative_widget_index_is_refused(self):
        # A valid list subscript, so letting one through would quietly rewrite
        # a widget counted from the end rather than the one asked for
        workflow = self.workflow(widgets_values=['a.safetensors', 'b.safetensors'])
        self.assertFalse(update_model_path(workflow, 1, -1, self.replacement))
        self.assertEqual(workflow['nodes'][0]['widgets_values'],
                         ['a.safetensors', 'b.safetensors'])

    def test_dict_shaped_widgets_values_are_written_by_key(self):
        workflow = self.workflow(widgets_values={'ckpt_name': 'missing.safetensors'})
        self.assertTrue(update_model_path(workflow, 1, 'ckpt_name', self.replacement,
                                          category='checkpoints'))
        self.assertEqual(workflow['nodes'][0]['widgets_values']['ckpt_name'],
                         'replacement.safetensors')

    def test_a_missing_dict_key_is_refused(self):
        workflow = self.workflow(widgets_values={'ckpt_name': 'missing.safetensors'})
        self.assertFalse(update_model_path(workflow, 1, 'nope', self.replacement))

    def test_a_model_nested_in_a_widget_object_is_written_in_place(self):
        workflow = self.workflow(
            type='Power Lora Loader (rgthree)',
            widgets_values=[{'on': True, 'lora': 'missing.safetensors', 'strength': 0.8}])
        self.assertTrue(update_model_path(workflow, 1, 0, self.replacement,
                                          category='checkpoints', nested_key='lora'))
        widget = workflow['nodes'][0]['widgets_values'][0]
        self.assertEqual(widget['lora'], 'replacement.safetensors')
        self.assertEqual(widget['strength'], 0.8, 'siblings must survive')

    def test_an_unexpected_widgets_values_type_is_refused(self):
        workflow = self.workflow(widgets_values='not-a-list')
        self.assertFalse(update_model_path(workflow, 1, 0, self.replacement))

    def test_the_category_of_the_chosen_model_decides_the_stored_form(self):
        # Not the category of the model that went missing: the path has to
        # resolve against the folder the replacement actually lives in
        loras = self.add_models('loras', ['grain.safetensors'])
        workflow = self.workflow(type='LoraLoader', widgets_values=['missing.safetensors'])
        update_model_path(workflow, 1, 0, os.path.join(loras, 'grain.safetensors'),
                          category='checkpoints',
                          resolved_model={'category': 'loras',
                                          'path': os.path.join(loras, 'grain.safetensors')})
        self.assertEqual(workflow['nodes'][0]['widgets_values'][0], 'grain.safetensors')


class SubgraphUpdateTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.base = self.add_models('loras', ['replacement.safetensors'])
        self.replacement = os.path.join(self.base, 'replacement.safetensors')

    def workflow(self):
        return {
            'nodes': [{'id': 5, 'type': 'sub-uuid-1', 'widgets_values': ['outer.safetensors']}],
            'definitions': {'subgraphs': [{
                'id': 'sub-uuid-1', 'name': 'Inner',
                'nodes': [{'id': 5, 'type': 'LoraLoader',
                           'widgets_values': ['inner.safetensors', 1.0]}],
            }]},
        }

    def test_a_node_inside_a_subgraph_definition_is_updated(self):
        workflow = self.workflow()
        self.assertTrue(update_model_path(workflow, 5, 0, self.replacement, category='loras',
                                          subgraph_id='sub-uuid-1', is_top_level=False))
        inner = workflow['definitions']['subgraphs'][0]['nodes'][0]
        self.assertEqual(inner['widgets_values'][0], 'replacement.safetensors')

    def test_a_top_level_instance_sharing_the_id_is_not_touched_instead(self):
        # The same node id exists in both places; only is_top_level tells them
        # apart, and picking wrong silently edits the other node
        workflow = self.workflow()
        update_model_path(workflow, 5, 0, self.replacement, category='loras',
                          subgraph_id='sub-uuid-1', is_top_level=False)
        self.assertEqual(workflow['nodes'][0]['widgets_values'][0], 'outer.safetensors')

    def test_a_subgraph_instance_at_the_top_level_is_updated_there(self):
        workflow = self.workflow()
        self.assertTrue(update_model_path(workflow, 5, 0, self.replacement, category='loras',
                                          subgraph_id='sub-uuid-1', is_top_level=True))
        self.assertEqual(workflow['nodes'][0]['widgets_values'][0], 'replacement.safetensors')
        inner = workflow['definitions']['subgraphs'][0]['nodes'][0]
        self.assertEqual(inner['widgets_values'][0], 'inner.safetensors')


class UpdateWorkflowNodesTests(LibraryTestCase):
    def test_several_resolutions_are_applied_in_one_pass(self):
        base = self.add_models('checkpoints', ['a_new.safetensors', 'b_new.safetensors'])
        workflow = {'nodes': [
            {'id': 1, 'type': 'CheckpointLoaderSimple', 'widgets_values': ['a_old.safetensors']},
            {'id': 2, 'type': 'CheckpointLoaderSimple', 'widgets_values': ['b_old.safetensors']},
        ]}
        update_workflow_nodes(workflow, [
            {'node_id': 1, 'widget_index': 0, 'category': 'checkpoints',
             'resolved_path': os.path.join(base, 'a_new.safetensors')},
            {'node_id': 2, 'widget_index': 0, 'category': 'checkpoints',
             'resolved_path': os.path.join(base, 'b_new.safetensors')},
        ])
        self.assertEqual([n['widgets_values'][0] for n in workflow['nodes']],
                         ['a_new.safetensors', 'b_new.safetensors'])

    def test_one_bad_mapping_does_not_stop_the_others(self):
        base = self.add_models('checkpoints', ['a_new.safetensors'])
        workflow = {'nodes': [
            {'id': 1, 'type': 'CheckpointLoaderSimple', 'widgets_values': ['a_old.safetensors']},
        ]}
        update_workflow_nodes(workflow, [
            {'node_id': 999, 'widget_index': 0, 'resolved_path': 'nowhere.safetensors'},
            {'node_id': 1, 'widget_index': 0, 'category': 'checkpoints',
             'resolved_path': os.path.join(base, 'a_new.safetensors')},
        ])
        self.assertEqual(workflow['nodes'][0]['widgets_values'][0], 'a_new.safetensors')


if __name__ == '__main__':
    unittest.main()
