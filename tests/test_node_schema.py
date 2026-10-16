"""
Introspection results are cached per node type - but "this type does not exist
yet" is not a result. Custom nodes can register after the first analysis.
"""

import os
import shutil
import sys
import tempfile
import threading
import types
import unittest

import fake_folder_paths
from core import node_schema
from support import write_file


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


class ConcurrentIntrospectionTests(unittest.TestCase):
    """
    Routes run analyses in worker threads, and ComfyUI's own prompt executor
    is a thread too - either can call `folder_paths.get_filename_list` while
    another thread is mid-introspection, and must get the real file list, not
    the sentinel meant for the introspecting thread alone.
    """

    def setUp(self):
        node_schema.clear_cache()
        self._saved_nodes = sys.modules.get('nodes')
        self.nodes = types.ModuleType('nodes')
        self.nodes.NODE_CLASS_MAPPINGS = {}
        sys.modules['nodes'] = self.nodes
        fake_folder_paths.reset()
        self.tmpdir = tempfile.mkdtemp(prefix='model-linker-node-schema-test-')
        write_file(os.path.join(self.tmpdir, 'grain.safetensors'))
        fake_folder_paths.add_category('loras', [self.tmpdir], {'.safetensors'})

    def tearDown(self):
        node_schema.clear_cache()
        if self._saved_nodes is None:
            sys.modules.pop('nodes', None)
        else:
            sys.modules['nodes'] = self._saved_nodes
        fake_folder_paths.reset()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_other_threads_see_the_real_list_during_introspection(self):
        hold_open = threading.Event()
        release = threading.Event()

        class SlowLoraLoader:
            @classmethod
            def INPUT_TYPES(cls):
                import folder_paths
                hold_open.set()
                release.wait(2)
                return {'required': {'lora_name': (folder_paths.get_filename_list('loras'),)}}

        self.nodes.NODE_CLASS_MAPPINGS['SlowLoraLoader'] = SlowLoraLoader

        result = {}

        def introspect():
            result['schema'] = node_schema.get_node_schema('SlowLoraLoader')

        introspecting = threading.Thread(target=introspect)
        introspecting.start()
        self.assertTrue(hold_open.wait(2), 'introspection did not start in time')

        import folder_paths
        other_thread_answer = folder_paths.get_filename_list('loras')
        release.set()
        introspecting.join(2)

        self.assertEqual(other_thread_answer, ['grain.safetensors'])
        self.assertEqual(result['schema']['categories'], {'lora_name': 'loras'})
