"""Scanning: what counts as a model, link handling, and the scan cache."""

import os
import time
import unittest

from core import scanner
from core.scanner import get_model_files, invalidate_cache, scan_directory

from support import LibraryTestCase, write_file


class ExtensionFilterTests(LibraryTestCase):
    def test_sidecar_files_are_not_catalogued(self):
        # A category that declares no extension filter used to mean "accept
        # everything", which put previews and metadata into the candidate pool
        self.add_models('checkpoints', [
            'flux1_dev.safetensors',
            'flux1_dev.civitai.info',
            'flux1_dev.preview.png',
            'flux1_dev.json',
            'notes.txt',
            'download.lock',
        ])
        found = {m['filename'] for m in get_model_files()}
        self.assertEqual(found, {'flux1_dev.safetensors'})

    def test_declared_extensions_are_honoured_alongside_model_ones(self):
        # A category may legitimately declare a non-model extension
        self.add_models('text_encoders',
                        ['umt5_xxl.safetensors', 'chat_template.jinja', 'preview.png'],
                        extensions=['.jinja'])
        found = {m['filename'] for m in get_model_files()}
        self.assertEqual(found, {'umt5_xxl.safetensors', 'chat_template.jinja'})

    def test_every_known_model_extension_is_accepted(self):
        names = ['a.ckpt', 'b.pt', 'c.pt2', 'd.bin', 'e.pth', 'f.safetensors',
                 'g.pkl', 'h.sft', 'i.onnx', 'j.gguf']
        self.add_models('checkpoints', names)
        self.assertEqual({m['filename'] for m in get_model_files()}, set(names))

    def test_hidden_directories_are_skipped(self):
        base = self.add_models('loras', ['visible.safetensors'])
        write_file(os.path.join(base, '.cache', 'hidden.safetensors'))
        self.assertEqual({m['filename'] for m in get_model_files()}, {'visible.safetensors'})

    def test_non_model_categories_are_skipped(self):
        self.add_models('configs', ['something.safetensors'])
        self.add_models('custom_nodes', ['other.safetensors'])
        self.assertEqual(get_model_files(), [])


class EntryShapeTests(LibraryTestCase):
    def test_relative_path_keeps_subfolders_and_native_separators(self):
        base = self.category_dir('loras')
        write_file(os.path.join(base, 'flux', 'style', 'grain.safetensors'))
        self.add_models('loras', [], directory=base)

        entry = get_model_files()[0]
        self.assertEqual(entry['filename'], 'grain.safetensors')
        self.assertEqual(entry['relative_path'], os.path.join('flux', 'style', 'grain.safetensors'))
        self.assertEqual(entry['base_directory'], base)
        self.assertEqual(entry['category'], 'loras')

    def test_real_path_is_recorded_for_every_entry(self):
        self.add_models('checkpoints', ['flux1_dev.safetensors'])
        entry = get_model_files()[0]
        self.assertTrue(entry['real_path'])
        self.assertTrue(os.path.isfile(entry['real_path']))


class LinkedDirectoryTests(LibraryTestCase):
    def test_a_linked_category_catalogues_the_same_physical_file(self):
        self.add_models('text_encoders', ['umt5_xxl_fp16.safetensors'])
        self.link_category('clip', 'text_encoders')

        entries = get_model_files()
        self.assertEqual(len(entries), 2, 'one entry per category reaching the file')
        self.assertEqual({e['category'] for e in entries}, {'text_encoders', 'clip'})

        # Two distinct paths, one physical file. Only a resolved path shows
        # this - os.path.normpath is lexical and leaves the aliases distinct.
        self.assertEqual(len({e['path'] for e in entries}), 2)
        self.assertEqual(len({os.path.normcase(e['real_path']) for e in entries}), 1)

    def test_a_link_pointing_at_an_ancestor_does_not_recurse_forever(self):
        base = self.category_dir('loras')
        write_file(os.path.join(base, 'a.safetensors'))
        nested = os.path.join(base, 'nested')
        os.makedirs(nested, exist_ok=True)
        from support import link_directory
        if not link_directory(base, os.path.join(nested, 'loop')):
            self.skipTest('filesystem links are not permitted in this environment')
        self.add_models('loras', [], directory=base)

        # Without the cycle guard this never returns
        entries = get_model_files()
        self.assertEqual({e['filename'] for e in entries}, {'a.safetensors'})


class ScanDirectoryTests(LibraryTestCase):
    def test_a_missing_directory_yields_nothing_rather_than_raising(self):
        self.assertEqual(scan_directory(os.path.join(self.root, 'nope'), set(), 'loras'), [])

    def test_visited_directories_are_reported_to_the_caller(self):
        base = self.category_dir('loras')
        write_file(os.path.join(base, 'sub', 'a.safetensors'))
        visited = []
        scan_directory(base, set(), 'loras', visited)
        self.assertIn(base, visited)
        self.assertIn(os.path.join(base, 'sub'), visited)


class ScanCacheTests(LibraryTestCase):
    def test_a_second_call_reuses_the_previous_scan(self):
        self.add_models('checkpoints', ['flux1_dev.safetensors'])
        first = get_model_files()
        self.assertIs(get_model_files(), first)

    def test_force_rescan_bypasses_the_cache(self):
        self.add_models('checkpoints', ['flux1_dev.safetensors'])
        first = get_model_files()
        self.assertIsNot(get_model_files(force_rescan=True), first)

    def test_a_new_model_on_disk_invalidates_the_cache(self):
        base = self.add_models('checkpoints', ['flux1_dev.safetensors'])
        self.assertEqual(len(get_model_files()), 1)

        # Directory mtime has one-second resolution on some filesystems, so
        # make sure the change is observable rather than racing it.
        time.sleep(1.1)
        write_file(os.path.join(base, 'flux1_schnell.safetensors'))

        self.assertEqual({m['filename'] for m in get_model_files()},
                         {'flux1_dev.safetensors', 'flux1_schnell.safetensors'})

    def test_a_newly_registered_category_invalidates_the_cache(self):
        # Custom nodes register folder paths after startup, so the cached
        # layout has to be part of what makes a cached scan valid
        self.add_models('checkpoints', ['flux1_dev.safetensors'])
        self.assertEqual(len(get_model_files()), 1)

        self.add_models('loras', ['grain.safetensors'])
        self.assertEqual(len(get_model_files()), 2)

    def test_invalidate_cache_forces_a_reread(self):
        self.add_models('checkpoints', ['flux1_dev.safetensors'])
        first = get_model_files()
        invalidate_cache()
        self.assertIsNot(get_model_files(), first)

    def test_a_failed_validity_check_falls_back_to_rescanning(self):
        # Validity rests on stat() calls against directories that can vanish
        # underneath it; the answer then has to be "rescan", not a failed
        # request, since scanning is what produces the whole analysis.
        self.add_models('checkpoints', ['flux1_dev.safetensors'])
        first = get_model_files()
        original = scanner._cache_is_valid

        def exploding(_cache):
            raise OSError('the model directory went away mid-check')

        scanner._cache_is_valid = exploding
        try:
            second = get_model_files()
        finally:
            scanner._cache_is_valid = original

        self.assertIsNot(second, first)
        self.assertEqual({m['filename'] for m in second}, {'flux1_dev.safetensors'})


if __name__ == '__main__':
    unittest.main()
