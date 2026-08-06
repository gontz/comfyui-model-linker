"""
Test suite for ComfyUI Model Linker.

Importing this package puts the extension on `sys.path` and installs the fake
`folder_paths` under its real name. Both have to happen before any `core`
module is imported: they capture `folder_paths` in a module global at import
time, and would otherwise capture nothing (ComfyUI is not on the path here) or,
worse, the real one and the developer's actual model library.
"""

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_EXTENSION_DIR = os.path.dirname(_TESTS_DIR)

for _path in (_EXTENSION_DIR, _TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Claim the name before core does. Assigned rather than setdefault: a real
# folder_paths already in sys.modules would point the tests at a live install.
import fake_folder_paths  # noqa: E402

sys.modules['folder_paths'] = fake_folder_paths
