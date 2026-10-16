"""
Introspection results are cached per node type - but "this type does not exist
yet" is not a result. Custom nodes can register after the first analysis.
"""

import sys
import types
import unittest

import fake_folder_paths
from core import node_schema


class LateRegistrationTests(unittest.TestCase):
    def setUp(self):
        node_schema.clear_cache()
        self._saved_nodes = sys.modules.get('nodes')
        self.nodes = types.ModuleType('nodes')
        self.nodes.NODE_CLASS_MAPPINGS = {}
        sys.modules['nodes'] = self.nodes
        fake_folder_paths.reset()
        fake_folder_paths.add_category('loras', [], {'.safetensors'})

    def tearDown(self):
        node_schema.clear_cache()
        if self._saved_nodes is None:
            sys.modules.pop('nodes', None)
        else:
            sys.modules['nodes'] = self._saved_nodes
        fake_folder_paths.reset()

    def test_type_registered_later_is_introspected(self):
        self.assertIsNone(node_schema.get_node_schema('LateLoraLoader'))

        class LateLoraLoader:
            @classmethod
            def INPUT_TYPES(cls):
                import folder_paths
                return {'required': {'lora_name': (folder_paths.get_filename_list('loras'),)}}

        self.nodes.NODE_CLASS_MAPPINGS['LateLoraLoader'] = LateLoraLoader
        schema = node_schema.get_node_schema('LateLoraLoader')
        self.assertIsNotNone(schema)
        # categories maps widget name -> folder category (node_schema.py:170)
        self.assertEqual(schema['categories'], {'lora_name': 'loras'})
