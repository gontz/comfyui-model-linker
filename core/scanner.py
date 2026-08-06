"""
Directory Scanner Module

Scans configured model directories and finds available model files.
"""

import os
import logging
from typing import Any, Dict, List, Optional, Tuple

# Import folder_paths lazily - it may not be available until ComfyUI is initialized
try:
    import folder_paths
except ImportError:
    folder_paths = None
    logging.warning("Model Linker: folder_paths not available yet - will retry later")

# Model file extensions to look for
# This matches folder_paths.supported_pt_extensions
MODEL_EXTENSIONS = {'.ckpt', '.pt', '.pt2', '.bin', '.pth', '.safetensors', '.pkl', '.sft', '.onnx', '.gguf'}


def get_model_directories() -> Dict[str, Tuple[List[str], set]]:
    """
    Get all configured model directories from folder_paths.
    
    Returns:
        Dictionary mapping category name to a tuple. ComfyUI may provide either:
        - (paths, extensions), or
        - (paths, extensions, recursive_flag)
    """
    global folder_paths
    
    if folder_paths is None:
        # Try to import again
        try:
            import folder_paths as fp
            folder_paths = fp
        except ImportError:
            logging.error("Model Linker: folder_paths still not available")
            return {}
    
    return folder_paths.folder_names_and_paths.copy()


def scan_directory(
    directory: str,
    extensions: set,
    category: str,
    visited_dirs: Optional[List[str]] = None
) -> List[Dict[str, str]]:
    """
    Recursively scan a single directory for model files.

    Args:
        directory: Absolute path to directory to scan
        extensions: Set of file extensions to look for
        category: Model category name (e.g., 'checkpoints', 'loras')
        visited_dirs: Optional list that every directory walked is appended to,
            so the caller can build a cache-validity signature from it

    Returns:
        List of dictionaries with model information:
        {
            'filename': 'model.safetensors',
            'path': 'absolute/path/to/model.safetensors',
            'real_path': path with junctions/symlinks resolved (physical identity),
            'relative_path': 'subfolder/model.safetensors' or 'model.safetensors',
            'category': 'checkpoints',
            'base_directory': 'absolute/path/to/base'
        }
    """
    models = []
    
    if not os.path.exists(directory) or not os.path.isdir(directory):
        logging.debug(f"Directory does not exist or is not accessible: {directory}")
        return models
    
    try:
        # Get absolute path and normalize
        base_directory = os.path.abspath(directory)
        
        # Directories already walked in this pass, by resolved path. Links are
        # followed so that models reached only through a linked folder are found,
        # which makes it possible to walk in circles - a link pointing at an
        # ancestor of itself would otherwise recurse until the request dies.
        seen_real_dirs = set()

        # Walk through directory recursively
        for root, dirs, files in os.walk(base_directory, followlinks=True):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            # Resolve the directory once per walk step so files reached through
            # junctions/symlinks can be traced back to the physical file they
            # share. Category roots are commonly links to one shared folder
            # (e.g. models\checkpoints, models\unet and models\diffusion_models
            # all pointing at the same directory), which makes one file appear
            # once per alias. Resolving per directory keeps this cheap: os.walk
            # visits each root exactly once, versus one syscall chain per file.
            try:
                real_root = os.path.realpath(root)
            except (OSError, ValueError):
                real_root = root

            # Having reached this physical directory once already, everything
            # below it is catalogued; descending again repeats the work at best
            # and never terminates at worst.
            real_key = os.path.normcase(real_root)
            if real_key in seen_real_dirs:
                dirs[:] = []
                continue
            seen_real_dirs.add(real_key)

            if visited_dirs is not None:
                visited_dirs.append(root)

            for filename in files:
                # Check if file has a model extension
                file_ext = os.path.splitext(filename)[1].lower()

                # Accept a file only if it plausibly is a model.
                #
                # A category may declare no extensions at all - core's `datasets`,
                # and any category a custom node registers without a filter. Treating
                # that as "accept every file" pulled sidecar files into the candidate
                # pool (.json, .civitai.info, .preview.png, .lock, .txt), which were
                # then offered as replacements for a missing model and could be
                # written into the workflow. An undeclared extension set means
                # "unknown", not "anything", so fall back to known model extensions.
                if extensions:
                    accepted = file_ext in extensions or file_ext in MODEL_EXTENSIONS
                else:
                    accepted = file_ext in MODEL_EXTENSIONS

                if accepted:
                    full_path = os.path.join(root, filename)
                    
                    # Calculate relative path from base directory
                    # IMPORTANT: Use OS-native path separators (backslashes on Windows)
                    # This matches ComfyUI's recursive_search format for get_filename_list
                    try:
                        relative_path = os.path.relpath(full_path, base_directory)
                        # DO NOT normalize - keep OS-native separators to match ComfyUI
                        # ComfyUI's get_filename_list uses os.path.relpath which returns
                        # backslashes on Windows, forward slashes on Unix
                    except ValueError:
                        # If paths are on different drives (Windows), use filename only
                        relative_path = filename

                    # Identity of the physical file behind this entry. Only a
                    # resolved path collapses link aliases - os.path.normpath is
                    # purely lexical and leaves each alias looking distinct.
                    # Files that are themselves links need their own resolution;
                    # everything else inherits the already-resolved directory.
                    try:
                        if os.path.islink(full_path):
                            real_path = os.path.realpath(full_path)
                        else:
                            real_path = os.path.join(real_root, filename)
                    except (OSError, ValueError):
                        real_path = full_path

                    models.append({
                        'filename': filename,
                        'path': full_path,
                        'real_path': real_path,
                        'relative_path': relative_path,
                        'category': category,
                        'base_directory': base_directory
                    })
    except (OSError, PermissionError) as e:
        logging.warning(f"Error scanning directory {directory}: {e}")
    
    return models


def scan_all_directories(visited_dirs: Optional[List[str]] = None) -> List[Dict[str, str]]:
    """
    Scan all configured model directories and return list of available models.

    Args:
        visited_dirs: Optional list collecting every directory walked, for cache
            validation by the caller

    Returns:
        List of dictionaries with model information (same format as scan_directory)
    """
    all_models = []
    directories = get_model_directories()

    for category, value in directories.items():
        # Skip categories that aren't typically model directories
        if category in ['custom_nodes', 'configs']:
            continue

        # Unpack folder_paths value flexibly: (paths, extensions) or (paths, extensions, recursive)
        paths = []
        extensions = set()
        try:
            if isinstance(value, (list, tuple)):
                if len(value) >= 2:
                    paths = value[0] or []
                    raw_exts = value[1]
                else:
                    # Unexpected format: treat value as paths
                    paths = list(value)
                    raw_exts = []
            elif isinstance(value, dict):
                paths = value.get('paths') or value.get('path') or []
                raw_exts = value.get('extensions') or []
            else:
                # Unknown format; skip category
                logging.debug(f"Unexpected folder_paths format for category {category}: {type(value)}")
                continue

            # Normalize extensions to a set[str]
            if isinstance(raw_exts, (list, tuple, set)):
                extensions = {str(e).lower() for e in raw_exts}
            elif raw_exts:
                extensions = {str(raw_exts).lower()}
        except Exception as e:
            logging.warning(f"Error interpreting folder_paths entry for {category}: {e}")
            continue

        for directory_path in paths:
            try:
                models = scan_directory(directory_path, extensions, category, visited_dirs)
                all_models.extend(models)
                logging.debug(f"Found {len(models)} models in {category}/{directory_path}")
            except Exception as e:
                logging.warning(f"Error scanning {category} directory {directory_path}: {e}")
    
    return all_models


# Cached result of the last full scan, with what it takes to know it is stale.
# A scan walks every model directory, which is by far the most expensive part of
# an analyse request, while the answer changes only when models are added or
# removed.
_scan_cache: Optional[Dict[str, Any]] = None


def _layout_signature() -> Any:
    """
    Signature of the configured category -> directories layout.

    Custom nodes can register folder paths after startup, so a cached scan is
    only valid while the set of categories and their directories is unchanged.
    """
    try:
        directories = get_model_directories()
    except Exception:
        return None

    layout = []
    for category, value in sorted(directories.items()):
        try:
            paths = value[0] if isinstance(value, (list, tuple)) and value else None
            layout.append((category, tuple(sorted(paths or ()))))
        except Exception:
            layout.append((category, None))
    return tuple(layout)


def _directory_signature(dirs: List[str]) -> Dict[str, Optional[float]]:
    """
    Map each scanned directory to its mtime.

    A directory's mtime changes when entries are added or removed from it, so
    comparing these is enough to detect model files appearing or disappearing.
    One stat per directory is far cheaper than re-walking every file.
    """
    signature: Dict[str, Optional[float]] = {}
    for directory in dirs:
        try:
            signature[directory] = os.path.getmtime(directory)
        except OSError:
            signature[directory] = None
    return signature


def _cache_is_valid(cache: Dict[str, Any]) -> bool:
    """Check whether a cached scan still reflects what is on disk."""
    if cache.get('layout') != _layout_signature():
        return False

    for directory, mtime in cache.get('dirs', {}).items():
        try:
            current = os.path.getmtime(directory)
        except OSError:
            current = None
        if current != mtime:
            return False
    return True


def invalidate_cache() -> None:
    """Drop the cached scan so the next call re-reads from disk."""
    global _scan_cache
    _scan_cache = None


def get_model_files(force_rescan: bool = False) -> List[Dict[str, str]]:
    """
    Get list of all available model files with metadata.

    This is the main entry point for getting model files. Results are cached and
    reused until a scanned directory changes on disk or the configured layout
    changes.

    Args:
        force_rescan: Skip the cache and re-read from disk

    Returns:
        List of model dictionaries (same format as scan_directory)
    """
    global _scan_cache

    if not force_rescan and _scan_cache is not None:
        try:
            if _cache_is_valid(_scan_cache):
                return _scan_cache['models']
        except Exception as e:
            logging.debug(f"Model Linker: cache validation failed, rescanning: {e}")

    visited_dirs: List[str] = []
    models = scan_all_directories(visited_dirs)

    _scan_cache = {
        'models': models,
        'dirs': _directory_signature(visited_dirs),
        'layout': _layout_signature(),
    }
    logging.debug(f"Model Linker: scanned {len(models)} models across {len(visited_dirs)} directories")
    return models

