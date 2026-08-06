"""
Node packs that store model references in their own shape.

The Lora Manager format is the awkward one: a *list of objects* in a single
widget slot, holding extension-less names. Neither half looks like a filename
to the generic scan, so both reading and writing go through an adapter.
"""

import copy
import unittest

from core.node_adapters import get_adapter, get_adapter_by_id
from core.workflow_analyzer import get_node_model_info
from core.workflow_updater import update_model_path

from support import LibraryTestCase

# Exactly the shape found in saved workflows
LORA_MANAGER_NODE = {
    'id': 7,
    'type': 'Lora Loader (LoraManager)',
    'widgets_values': [
        '<lora:DP_Grainy_Illustration:1.00> <lora:thegeorgia_lora_flux_dev:0.20>',
        [
            {'name': 'DP_Grainy_Illustration', 'strength': 1, 'active': True, 'clipStrength': 1},
            {'name': 'thegeorgia_lora_flux_dev', 'strength': '0.20', 'active': False,
             'clipStrength': '0.20'},
        ],
    ],
}


class AdapterRegistryTests(unittest.TestCase):
    def test_lora_manager_has_an_adapter(self):
        self.assertIsNotNone(get_adapter('Lora Loader (LoraManager)'))

    def test_an_adapter_can_be_recovered_by_id_for_writing_back(self):
        adapter = get_adapter('Lora Loader (LoraManager)')
        self.assertIs(get_adapter_by_id(adapter.adapter_id), adapter)

    def test_rgthree_deliberately_has_none(self):
        # It stores objects directly in widget slots, which the generic
        # nested-key scan already reads
        self.assertIsNone(get_adapter('Power Lora Loader (rgthree)'))

    def test_ordinary_loaders_have_none(self):
        self.assertIsNone(get_adapter('CheckpointLoaderSimple'))
        self.assertIsNone(get_adapter(None))


class LoraManagerReadTests(unittest.TestCase):
    def setUp(self):
        self.refs = get_node_model_info(copy.deepcopy(LORA_MANAGER_NODE))

    def test_every_lora_in_the_list_is_extracted(self):
        self.assertEqual([r['original_path'] for r in self.refs],
                         ['DP_Grainy_Illustration', 'thegeorgia_lora_flux_dev'])

    def test_each_lora_is_identified_by_its_position_in_the_list(self):
        # Several loras share one node and widget index; without list_index
        # they collide in the frontend's element ids and pending selections
        self.assertEqual([r['list_index'] for r in self.refs], [0, 1])

    def test_the_list_is_found_by_shape_not_by_a_fixed_index(self):
        # Its position differs between node types and across pack releases
        moved = copy.deepcopy(LORA_MANAGER_NODE)
        moved['widgets_values'] = ['extra', 'padding'] + moved['widgets_values']
        refs = get_node_model_info(moved)
        self.assertEqual([r['widget_index'] for r in refs], [3, 3])

    def test_references_are_categorised_and_attributed_to_the_adapter(self):
        for ref in self.refs:
            self.assertEqual(ref['category'], 'loras')
            self.assertEqual(ref['adapter_id'], 'lora-manager')


class LoraManagerWriteTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.node = copy.deepcopy(LORA_MANAGER_NODE)
        self.workflow = {'nodes': [self.node]}
        self.base = self.add_models('loras', ['replacement_lora.safetensors'])

    def replace(self, list_index=1):
        import os
        return update_model_path(
            self.workflow, 7, 1,
            os.path.join(self.base, 'replacement_lora.safetensors'),
            category='loras', is_top_level=True,
            list_index=list_index, adapter_id='lora-manager')

    def test_the_targeted_entry_is_rewritten(self):
        self.assertTrue(self.replace())
        self.assertEqual(self.node['widgets_values'][1][1]['name'], 'replacement_lora')

    def test_the_extension_is_dropped_as_the_pack_expects(self):
        self.replace()
        self.assertNotIn('.safetensors', self.node['widgets_values'][1][1]['name'])

    def test_no_backslashes_are_written(self):
        # The pack's own lookup uses forward slashes
        self.replace()
        self.assertNotIn('\\', self.node['widgets_values'][1][1]['name'])

    def test_sibling_entries_are_left_alone(self):
        self.replace()
        self.assertEqual(self.node['widgets_values'][1][0]['name'], 'DP_Grainy_Illustration')

    def test_strengths_and_flags_survive_the_rewrite(self):
        self.replace()
        entry = self.node['widgets_values'][1][1]
        self.assertEqual(entry['strength'], '0.20')
        self.assertIs(entry['active'], False)

    def test_the_companion_text_token_is_kept_in_sync(self):
        self.replace()
        text = self.node['widgets_values'][0]
        self.assertNotIn('thegeorgia_lora_flux_dev', text)
        self.assertIn('replacement_lora', text)

    def test_the_other_text_token_is_untouched(self):
        self.replace()
        self.assertIn('<lora:DP_Grainy_Illustration:1.00>', self.node['widgets_values'][0])

    def test_a_list_index_past_the_end_changes_nothing(self):
        self.assertFalse(self.replace(list_index=99))
        self.assertEqual(self.node['widgets_values'][1][1]['name'], 'thegeorgia_lora_flux_dev')


if __name__ == '__main__':
    unittest.main()
