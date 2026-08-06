"""
Shared fixtures: a synthetic model library on disk, and a base TestCase.

The library is built per test in a temporary directory so nothing depends on
what the developer happens to have installed. Where a test needs the linked
category folders that motivate half this extension's design, it asks for a real
link on disk - see `link_directory`.
"""

import os
import shutil
import sys
import tempfile
import unittest

import fake_folder_paths


def link_directory(target: str, link: str) -> bool:
    """
    Link `link` to the existing directory `target`, as an install does when
    `models/clip` is made to point at `models/text_encoders`.

    Returns False when the platform will not allow it, so a test can skip
    rather than fail: on Windows a symlink needs Developer Mode or elevation,
    though a junction usually does not.
    """
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError, AttributeError):
        pass

    if sys.platform == 'win32':
        try:
            import _winapi
            _winapi.CreateJunction(target, link)
            return True
        except (OSError, ImportError, AttributeError):
            pass

    return False


def write_file(path: str, content: str = 'x') -> str:
    """Create a file (and its parents), returning the path."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(content)
    return path


class LibraryTestCase(unittest.TestCase):
    """
    A temporary model library, wired into the fake folder_paths.

    Subclasses build their own layout in `setUp` via `add_models`; the default
    layout is intentionally minimal so each test states what it depends on.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='model-linker-test-')
        self.models = os.path.join(self.root, 'models')
        os.makedirs(self.models, exist_ok=True)
        fake_folder_paths.reset()

        # The scan cache is a module global and would otherwise carry results
        # from the previous test's library into this one.
        from core import scanner
        scanner.invalidate_cache()

    def tearDown(self):
        from core import scanner
        scanner.invalidate_cache()
        fake_folder_paths.reset()
        shutil.rmtree(self.root, ignore_errors=True)

    # -- library construction -------------------------------------------------

    def category_dir(self, name: str) -> str:
        path = os.path.join(self.models, name)
        os.makedirs(path, exist_ok=True)
        return path

    def add_models(self, category: str, filenames, extensions=(), directory=None):
        """
        Create `filenames` inside a category's directory and register it.

        `extensions` is what the category declares to ComfyUI; leaving it empty
        reproduces a category registered without a filter.
        """
        base = directory or self.category_dir(category)
        for filename in filenames:
            write_file(os.path.join(base, filename))
        existing = fake_folder_paths.folder_names_and_paths.get(category)
        paths = list(existing[0]) if existing else []
        if base not in paths:
            paths.append(base)
        declared = set(existing[1]) if existing else set()
        declared.update(e.lower() for e in extensions)
        fake_folder_paths.add_category(category, paths, declared)
        return base

    def link_category(self, name: str, target_category: str) -> str:
        """
        Register `name` as a second category pointing at another's directory
        through a real filesystem link. Skips the test if the platform refuses.
        """
        target = self.category_dir(target_category)
        link = os.path.join(self.models, name)
        if not link_directory(target, link):
            self.skipTest('filesystem links are not permitted in this environment')
        existing = fake_folder_paths.folder_names_and_paths.get(target_category)
        fake_folder_paths.add_category(name, [link], set(existing[1]) if existing else set())
        return link

    def alias_category(self, name: str, target_category: str) -> str:
        """
        Register `name` against the *same* directory as another category - the
        no-link half of the problem, which ComfyUI itself does for `unet` and
        `diffusion_models`. Needs no filesystem support.
        """
        target = self.category_dir(target_category)
        existing = fake_folder_paths.folder_names_and_paths.get(target_category)
        fake_folder_paths.add_category(name, [target], set(existing[1]) if existing else set())
        return target
