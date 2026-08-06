"""
Reveal a model in the desktop file manager.

This runs a program on the machine hosting ComfyUI, at the request of a
browser, so it is deliberately narrow:

- the client names a model by (category, filename); an absolute path from the
  client is never honoured, so there is nothing to point somewhere else
- the resolved file must sit inside a directory ComfyUI registered for that
  category, which is what stops `../` from walking out of it
- the file manager is launched with an argument list, never a shell string

The caller is expected to have established that the request is local - opening
a window on the server is meaningless to a remote user and surprising to
whoever is sitting at it. See the route in `__init__.py`.
"""

import logging
import os
import subprocess
import sys
from typing import Optional, Tuple


def _is_within(path: str, base: str) -> bool:
    """
    Whether `path` is inside `base`.

    Checked both lexically and with links resolved, and either is enough.
    Lexical containment is what refuses `../`; the resolved comparison is what
    keeps a genuine model reachable when its category directory is a junction
    to somewhere else, which is the normal arrangement rather than the odd one.
    """
    for resolve in (False, True):
        try:
            if resolve:
                candidate, root = os.path.realpath(path), os.path.realpath(base)
            else:
                candidate, root = os.path.abspath(path), os.path.abspath(base)
            if os.path.commonpath([candidate, root]) == root:
                return True
        except (ValueError, OSError):
            # Different drives on Windows, or an unreadable path
            continue
    return False


def locate_model(category: str, filename: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolve (category, filename) to a file on disk, refusing anything outside
    the category's own directories.

    Returns (path, None) on success, or (None, reason) when it will not be
    revealed. The reason is for the log and the response, not for the user.
    """
    if not category or not filename:
        return None, 'category and filename are required'

    # Only names relative to a category directory are ever accepted.
    #
    # os.path.isabs is not enough on its own. On Windows it reports False for
    # "/etc/passwd" - since Python 3.13 a leading slash without a drive counts
    # as root-relative rather than absolute - while os.path.join happily turns
    # that into "C:/etc/passwd", straight out of the category directory. A
    # drive-relative "C:name" escapes the same way.
    drive, _ = os.path.splitdrive(filename)
    if (os.path.isabs(filename) or drive
            or filename.startswith('/') or filename.startswith('\\')):
        return None, 'filename must be relative to its category directory'

    try:
        import folder_paths
    except ImportError:
        return None, 'folder_paths is not available'

    try:
        path = folder_paths.get_full_path(category, filename)
    except Exception as e:
        logging.debug(f"Model Linker: could not resolve {category}/{filename}: {e}")
        path = None

    if not path or not os.path.isfile(path):
        return None, 'no such model'

    try:
        bases = folder_paths.get_folder_paths(category) or []
    except Exception:
        bases = []

    if not any(_is_within(path, base) for base in bases):
        return None, 'model is outside its category directory'

    return path, None


def reveal(path: str) -> Tuple[bool, Optional[str]]:
    """
    Open the desktop file manager with `path` selected.

    Returns (True, None), or (False, reason) when the platform has no way to
    do it or the file manager could not be started.
    """
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        return False, 'containing folder does not exist'

    if sys.platform == 'win32':
        # Explorer wants the comma form, and exits 1 even when it worked, so
        # its return code says nothing worth checking.
        command = ['explorer', f'/select,{path}']
    elif sys.platform == 'darwin':
        command = ['open', '-R', path]
    else:
        # No portable "select the file" on Linux desktops; the folder is the
        # useful part and xdg-open is the one thing broadly present.
        command = ['xdg-open', directory]

    try:
        # No shell: every argument is passed through as data. Detached, since
        # a file manager outlives the request that opened it.
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, None
    except (OSError, ValueError) as e:
        logging.warning(f"Model Linker: could not open a file manager: {e}")
        return False, 'no file manager available'
