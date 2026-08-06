"""
Stand-in for ComfyUI's `folder_paths`.

The core modules do `import folder_paths` at import time and hold the module
object, so the tests install this one under that name before importing them.
Every test then reconfigures it in place, which every holder sees.

Behaviour deliberately mirrors ComfyUI's own, including the part the scanner
has to defend against: `filter_files_extensions` treats an *empty* extension
set as "accept every file", which is how sidecar files (.json, .preview.png,
.civitai.info) reach a category that registered no filter.
"""

import os
from typing import Dict, Iterator, List, Optional, Set, Tuple

# category -> (paths, extensions), the shape ComfyUI exposes
folder_names_and_paths: Dict[str, Tuple[List[str], Set[str]]] = {}


def reset() -> None:
    """Forget every configured category."""
    folder_names_and_paths.clear()


def add_category(name: str, paths, extensions=()) -> None:
    """Register a category, as ComfyUI or a custom node pack would."""
    if isinstance(paths, str):
        paths = [paths]
    folder_names_and_paths[name] = (list(paths), {e.lower() for e in extensions})


def get_folder_paths(category: str) -> List[str]:
    entry = folder_names_and_paths.get(category)
    return list(entry[0]) if entry else []


def _iter_files(category: str) -> Iterator[Tuple[str, str]]:
    entry = folder_names_and_paths.get(category)
    if not entry:
        return
    paths, extensions = entry[0], entry[1]
    for base in paths:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base, followlinks=True):
            for filename in files:
                extension = os.path.splitext(filename)[1].lower()
                # An empty set means "no filter" here, matching ComfyUI
                if extensions and extension not in extensions:
                    continue
                full = os.path.join(root, filename)
                yield os.path.relpath(full, base), full


def get_filename_list(category: str) -> List[str]:
    return sorted({relative for relative, _full in _iter_files(category)})


def get_full_path(category: str, filename: str) -> Optional[str]:
    entry = folder_names_and_paths.get(category)
    if not entry or not filename:
        return None
    for base in entry[0]:
        candidate = os.path.join(base, filename)
        if os.path.isfile(candidate):
            return candidate
    return None
