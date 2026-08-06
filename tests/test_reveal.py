"""
Revealing a model in the file manager.

This runs a program on the machine hosting ComfyUI at a browser's request, so
what it refuses matters more than what it opens.
"""

import os
import unittest

from core import reveal as reveal_module
from core.reveal import _is_within, locate_model, reveal

from support import LibraryTestCase


class ContainmentTests(LibraryTestCase):
    def test_a_file_inside_the_base_is_accepted(self):
        base = self.category_dir('loras')
        self.assertTrue(_is_within(os.path.join(base, 'a.safetensors'), base))

    def test_a_file_outside_the_base_is_refused(self):
        base = self.category_dir('loras')
        self.assertFalse(_is_within(os.path.join(self.root, 'elsewhere.safetensors'), base))

    def test_a_traversal_out_of_the_base_is_refused(self):
        base = self.category_dir('loras')
        self.assertFalse(_is_within(os.path.join(base, '..', '..', 'secrets.txt'), base))

    def test_a_linked_category_directory_still_contains_its_models(self):
        # The normal arrangement, not the odd one: comparing only resolved
        # paths, or only lexical ones, breaks one of these two cases
        self.add_models('text_encoders', ['umt5.safetensors'])
        link = self.link_category('clip', 'text_encoders')
        self.assertTrue(_is_within(os.path.join(link, 'umt5.safetensors'), link))


class LocateModelTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.base = self.add_models('loras', ['grain.safetensors'])

    def test_a_real_model_is_located(self):
        path, problem = locate_model('loras', 'grain.safetensors')
        self.assertIsNone(problem)
        self.assertEqual(os.path.normcase(path),
                         os.path.normcase(os.path.join(self.base, 'grain.safetensors')))

    def test_a_model_in_a_subfolder_is_located(self):
        from support import write_file
        write_file(os.path.join(self.base, 'flux', 'nested.safetensors'))
        path, problem = locate_model('loras', os.path.join('flux', 'nested.safetensors'))
        self.assertIsNone(problem)
        self.assertTrue(path.endswith('nested.safetensors'))

    def test_a_missing_model_is_refused(self):
        path, problem = locate_model('loras', 'nope.safetensors')
        self.assertIsNone(path)
        self.assertEqual(problem, 'no such model')

    def test_an_absolute_path_from_the_client_is_never_honoured(self):
        # "/etc/passwd" is here because os.path.isabs calls it relative on
        # Windows while os.path.join turns it into "C:/etc/passwd"; "C:model"
        # is drive-relative and escapes the same way.
        for candidate in (r'C:\Windows\System32\notepad.exe', '/etc/passwd',
                          r'\\server\share\file', r'\Windows\notepad.exe',
                          'C:model.safetensors'):
            path, problem = locate_model('loras', candidate)
            self.assertIsNone(path, candidate)
            self.assertIn('relative', problem, candidate)

    def test_a_traversal_attempt_is_refused(self):
        from support import write_file
        secret = write_file(os.path.join(self.root, 'secret.txt'), 'private')
        self.assertTrue(os.path.isfile(secret))
        relative = os.path.join('..', '..', 'secret.txt')
        path, problem = locate_model('loras', relative)
        self.assertIsNone(path, f'traversal reached {path}')
        self.assertIsNotNone(problem)

    def test_an_unregistered_category_is_refused(self):
        path, problem = locate_model('not_a_category', 'grain.safetensors')
        self.assertIsNone(path)

    def test_empty_arguments_are_refused(self):
        for category, filename in (('', 'a.safetensors'), ('loras', ''), (None, None)):
            path, problem = locate_model(category, filename)
            self.assertIsNone(path)
            self.assertIsNotNone(problem)

    def test_a_directory_is_not_a_model(self):
        os.makedirs(os.path.join(self.base, 'subdir'), exist_ok=True)
        path, problem = locate_model('loras', 'subdir')
        self.assertIsNone(path)


class RevealTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.base = self.add_models('loras', ['grain.safetensors'])
        self.model = os.path.join(self.base, 'grain.safetensors')
        self.launched = []
        self._real_popen = reveal_module.subprocess.Popen
        reveal_module.subprocess.Popen = lambda command, **kwargs: self.launched.append(command)

    def tearDown(self):
        reveal_module.subprocess.Popen = self._real_popen
        super().tearDown()

    def test_the_file_manager_is_launched_for_a_real_model(self):
        opened, problem = reveal(self.model)
        self.assertTrue(opened, problem)
        self.assertEqual(len(self.launched), 1)

    def test_the_path_is_passed_as_an_argument_never_as_a_shell_string(self):
        # A filename is not a command; passing a list is what keeps it data
        reveal(self.model)
        command = self.launched[0]
        self.assertIsInstance(command, list)
        self.assertTrue(any(self.model in str(part) for part in command), command)

    def test_a_folder_that_does_not_exist_is_reported_rather_than_launched(self):
        opened, problem = reveal(os.path.join(self.root, 'gone', 'model.safetensors'))
        self.assertFalse(opened)
        self.assertEqual(self.launched, [])

    def test_a_missing_file_manager_is_reported_rather_than_raised(self):
        def refuse(*_args, **_kwargs):
            raise FileNotFoundError('no file manager on this system')

        reveal_module.subprocess.Popen = refuse
        opened, problem = reveal(self.model)
        self.assertFalse(opened)
        self.assertEqual(problem, 'no file manager available')


if __name__ == '__main__':
    unittest.main()
