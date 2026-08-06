"""
User Overrides Module

Stores and retrieves user-selected replacements so future analyses
can auto-suggest or auto-resolve to the saved match.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional

from .matcher import normalize_filename


def _data_dir() -> str:
    """Return the directory path for storing persistence data."""
    # Place under the extension directory in a `data` subfolder
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    data = os.path.join(base, "data")
    os.makedirs(data, exist_ok=True)
    return data


def _overrides_path() -> str:
    return os.path.join(_data_dir(), "overrides.json")


def get_overrides_path() -> str:
    """Public helper to report the absolute path used for overrides.json."""
    return _overrides_path()


def _default_doc() -> Dict[str, Any]:
    return {"version": 1, "mappings": []}


def load_overrides() -> Dict[str, Any]:
    """Load overrides JSON; returns a dictionary with key 'mappings' (list)."""
    path = _overrides_path()
    try:
        if not os.path.exists(path):
            return _default_doc()
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
            # Basic shape validation
            if not isinstance(doc, dict) or "mappings" not in doc:
                return _default_doc()
            if not isinstance(doc["mappings"], list):
                doc["mappings"] = []
            return doc
    except Exception as e:
        logging.warning(f"Model Linker: Failed to load overrides: {e}")
        return _default_doc()


def _save_overrides(doc: Dict[str, Any]) -> None:
    """
    Write the overrides document atomically.

    Writing in place leaves a window where a crash or a full disk truncates the
    file and loses every saved selection. Writing to a temporary file in the
    same directory and renaming means readers only ever see a complete document:
    os.replace is atomic on Windows and POSIX alike.
    """
    path = _overrides_path()
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(path), prefix=".overrides-", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
        logging.info(f"Model Linker: Saved overrides to {path}")
    except Exception as e:
        logging.warning(f"Model Linker: Failed to save overrides: {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def delete_override(key: str) -> bool:
    """Delete a single override by its key. Returns True if removed."""
    if not key:
        return False
    doc = load_overrides()
    before = len(doc.get("mappings", []))
    doc["mappings"] = [m for m in doc.get("mappings", []) if m.get("key") != key]
    after = len(doc.get("mappings", []))
    if after != before:
        _save_overrides(doc)
        return True
    return False


def clear_overrides() -> None:
    """Clear all overrides (resets the document)."""
    _save_overrides(_default_doc())


def replace_overrides(new_doc: Dict[str, Any]) -> bool:
    """Replace overrides with a new document if valid. Returns True on success."""
    try:
        if not isinstance(new_doc, dict):
            return False
        mappings = new_doc.get("mappings")
        if mappings is None:
            # Allow passing list directly
            if isinstance(new_doc, list):
                mappings = new_doc
                new_doc = {"version": 1, "mappings": mappings}
            else:
                return False
        if not isinstance(mappings, list):
            return False
        # Basic sanitize: only keep dict entries with key and path
        clean: List[Dict[str, Any]] = []
        for m in mappings:
            if isinstance(m, dict) and m.get("key") and m.get("path"):
                clean.append(m)
        new_doc["version"] = int(new_doc.get("version", 1))
        new_doc["mappings"] = clean
        _save_overrides(new_doc)
        return True
    except Exception:
        logging.exception("Model Linker: Failed to replace overrides")
        return False


def _normalize_category(category: Optional[str]) -> str:
    """Reduce a category to the form used inside override keys."""
    cat = (category or "").strip().lower() or "any"
    if cat in ("unknown", "none", "undefined"):
        cat = "any"
    return cat


def _make_keys(original_path: str, category: Optional[str]) -> List[str]:
    """
    Produce candidate keys for lookup.
    Primary: category + normalized filename; Fallback: any + normalized filename
    """
    filename = os.path.basename(original_path or "").strip()
    norm = normalize_filename(filename) if filename else ""
    cat = _normalize_category(category)
    keys = [f"{cat}:{norm}"]
    if cat != "any":
        keys.append(f"any:{norm}")
    return keys


def find_override_path(original_path: str, category: Optional[str]) -> Optional[str]:
    """Return stored absolute path for a given (original_path, category), if any."""
    doc = load_overrides()
    keys = _make_keys(original_path, category)
    # Build quick lookup
    lookup: Dict[str, Dict[str, Any]] = {}
    for m in doc.get("mappings", []):
        k = m.get("key")
        if isinstance(k, str):
            lookup[k] = m
    for k in keys:
        m = lookup.get(k)
        if m and m.get("path"):
            return m.get("path")

    # Fallback: match by normalized filename regardless of saved category
    try:
        filename = os.path.basename(original_path or "").strip()
        norm = normalize_filename(filename) if filename else ""
        if norm:
            for m in doc.get("mappings", []):
                key = m.get("key") or ""
                if isinstance(key, str) and ":" in key:
                    _, saved_norm = key.split(":", 1)
                    if saved_norm == norm and m.get("path"):
                        return m.get("path")
    except Exception:
        pass
    return None


def find_override_model(original_path: str, category: Optional[str], available_models: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Return the model dict from available_models for a saved override, if present.
    """
    saved_path = find_override_path(original_path, category)
    if not saved_path:
        return None
    try:
        saved_norm = os.path.normpath(saved_path)
    except Exception:
        saved_norm = saved_path
    for m in available_models:
        p = m.get("path")
        try:
            if p and os.path.normpath(p) == saved_norm:
                return m
        except Exception:
            if p == saved_path:
                return m
    # If not found (file moved/removed), do not return stale override
    return None


def _build_override_entry(
    original_path: str,
    category: Optional[str],
    resolved: Dict[str, Any] | None = None,
    resolved_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build a single override entry, or None if there is nothing to record."""
    if isinstance(resolved, dict):
        path = resolved.get("path") or resolved_path
    else:
        path = resolved_path
    if not original_path or not path:
        return None

    entry: Dict[str, Any] = {
        "key": _make_keys(original_path, category)[0],  # primary key (category-aware)
        "original_filename": os.path.basename(original_path),
        # Normalize category consistently with _make_keys
        "category": _normalize_category(category),
        "path": path,
    }
    # Optional metadata for convenience
    if isinstance(resolved, dict):
        for k in ("filename", "relative_path", "base_directory"):
            if k in resolved:
                entry[k] = resolved[k]
    return entry


def _upsert_entry(mappings: List[Dict[str, Any]], entry: Dict[str, Any]) -> None:
    """Replace the mapping with the same key, or append if there is none."""
    for i, m in enumerate(mappings):
        if m.get("key") == entry["key"]:
            mappings[i] = entry
            return
    mappings.append(entry)


def record_overrides(selections: List[Dict[str, Any]]) -> int:
    """
    Record several user selections with a single read and a single write.

    Resolving a workflow saves one override per relinked model. Recording them
    one at a time reloads and rewrites the whole document for each, which both
    wastes work and multiplies the number of moments a failed write could leave
    the file behind.

    Args:
        selections: dicts with keys `original_path`, `category`, and `resolved`
            (model dict from the scanner) and/or `resolved_path`

    Returns:
        Number of overrides recorded.
    """
    entries = []
    for selection in selections:
        entry = _build_override_entry(
            selection.get("original_path"),
            selection.get("category"),
            selection.get("resolved"),
            selection.get("resolved_path"),
        )
        if entry:
            entries.append(entry)

    if not entries:
        return 0

    doc = load_overrides()
    mappings = doc.get("mappings", [])
    for entry in entries:
        _upsert_entry(mappings, entry)
    doc["mappings"] = mappings
    _save_overrides(doc)
    return len(entries)


def record_override(original_path: str, category: Optional[str], resolved: Dict[str, Any] | None = None, resolved_path: Optional[str] = None) -> bool:
    """
    Record a user-selected override.

    Args:
        original_path: original missing value from workflow
        category: model category if known
        resolved: model dict from scanner (preferred)
        resolved_path: absolute path if model dict not provided

    Returns:
        True if the override file was updated, else False.
    """
    return record_overrides([{
        "original_path": original_path,
        "category": category,
        "resolved": resolved,
        "resolved_path": resolved_path,
    }]) > 0
