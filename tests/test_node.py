"""The Model Linker node marks shared workflows and never executes."""

import importlib.util
import os
import unittest

EXTENSION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NodeTests(unittest.TestCase):
    def test_node_has_no_inputs_or_outputs(self):
        spec = importlib.util.spec_from_file_location(
            'model_linker_under_test', os.path.join(EXTENSION_DIR, '__init__.py'),
            submodule_search_locations=[EXTENSION_DIR])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # route setup fails quietly without ComfyUI's server
        node = module.NODE_CLASS_MAPPINGS['ModelLinker']
        self.assertEqual(node.INPUT_TYPES(), {'required': {}})
        self.assertEqual(node.RETURN_TYPES, ())
        self.assertFalse(getattr(node, 'OUTPUT_NODE', False))
        self.assertEqual(module.NODE_DISPLAY_NAME_MAPPINGS['ModelLinker'], 'Model Linker')
