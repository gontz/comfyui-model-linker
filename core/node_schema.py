"""
Node Schema Introspection

Works out which of a node's widgets hold models, and which folder each draws
from, by asking the node class itself rather than consulting a hand-maintained
table of node types.

ComfyUI fills a model widget with `folder_paths.get_filename_list(category)`.
Standing in for that function while a node declares its inputs records the
category each widget asked for, which identifies model widgets on every
installed node - including custom ones this extension has never heard of. On
this development machine that is 526 node types with model widgets, against 22
in the hand-written table.

Both node APIs are covered by one path: V3 nodes expose `INPUT_TYPES()` through
a compatibility shim over `define_schema()`.
"""

import logging
import threading
from typing import Any, Dict, List, Optional

# Marks a value as "this widget was filled from folder category X". Uses NUL
# bytes so it cannot collide with a real filename.
_SENTINEL = "\x00model_linker_category\x00"

# INPUT_TYPES entries of these types become entries in widgets_values. Typed
# inputs such as MODEL or CLIP are graph links and occupy no widget slot, so
# they must not shift widget positions.
_WIDGET_INPUT_TYPES = {"BOOLEAN", "COMBO", "FLOAT", "INT", "STRING"}

# Introspection swaps a module-level function, so only one may run at a time
_INTROSPECTION_LOCK = threading.RLock()

# node type -> {'categories': {input_name: category}, 'widget_names': [...]}
_SCHEMA_CACHE: Dict[str, Optional[Dict[str, Any]]] = {}


def _get_node_class(node_type: str) -> Any:
    """Look up an installed node class by its workflow type name."""
    try:
        import nodes
    except ImportError:
        return None
    try:
        return (nodes.NODE_CLASS_MAPPINGS or {}).get(node_type)
    except Exception:
        return None


def _traced_get_filename_list(category: Any, *args: Any, **kwargs: Any) -> List[str]:
    """Stand-in that reports which folder was asked for instead of its contents."""
    return [f"{_SENTINEL}{category}"]


def _patch_function_globals(func: Any, original: Any, patched: List[Any]) -> None:
    """
    Redirect a module-level `get_filename_list` binding.

    Nodes that did `from folder_paths import get_filename_list` hold their own
    reference, which replacing the attribute on the module would not reach.
    """
    namespace = getattr(func, "__globals__", None)
    if not isinstance(namespace, dict):
        return
    for name, value in list(namespace.items()):
        if value is original:
            namespace[name] = _traced_get_filename_list
            patched.append((namespace, name, value))


def _category_from_choices(choices: Any) -> Optional[str]:
    """Recover the folder category recorded in a widget's choice list."""
    if isinstance(choices, str):
        return choices[len(_SENTINEL):] if choices.startswith(_SENTINEL) else None
    if isinstance(choices, (list, tuple)):
        for choice in choices:
            category = _category_from_choices(choice)
            if category:
                return category
    return None


def _is_widget_spec(spec: Any) -> bool:
    """Whether an INPUT_TYPES entry occupies a slot in widgets_values."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return False
    input_type = spec[0]
    # A list of choices is a COMBO
    if isinstance(input_type, (list, tuple)):
        return True
    if isinstance(input_type, str):
        return input_type.strip().upper() in _WIDGET_INPUT_TYPES
    return False


def _adds_control_widget(spec: Any) -> bool:
    """
    Whether this input is followed by an extra generated widget.

    Seeds declared with `control_after_generate` serialise a second value right
    after their own, which shifts every widget that follows.
    """
    if not isinstance(spec, (list, tuple)) or len(spec) < 2:
        return False
    options = spec[1]
    return isinstance(options, dict) and bool(options.get("control_after_generate"))


def _introspect(node_type: str) -> Optional[Dict[str, Any]]:
    """Read one node class's declared inputs. Returns None when unavailable."""
    node_class = _get_node_class(node_type)
    if node_class is None:
        return None

    input_types = getattr(node_class, "INPUT_TYPES", None)
    if not callable(input_types):
        return None

    try:
        import folder_paths
    except ImportError:
        return None

    original = getattr(folder_paths, "get_filename_list", None)
    if not callable(original):
        return None

    patched: List[Any] = []
    with _INTROSPECTION_LOCK:
        try:
            folder_paths.get_filename_list = _traced_get_filename_list
            # Reach bindings the node captured at import time
            _patch_function_globals(getattr(input_types, "__func__", input_types), original, patched)
            schema_func = getattr(node_class, "define_schema", None)
            if callable(schema_func):
                _patch_function_globals(getattr(schema_func, "__func__", schema_func), original, patched)

            declared = input_types()
        except Exception as e:
            logging.debug(f"Model Linker: could not read inputs of {node_type}: {e}")
            return None
        finally:
            folder_paths.get_filename_list = original
            for namespace, name, value in patched:
                namespace[name] = value

    if not isinstance(declared, dict):
        return None

    categories: Dict[str, str] = {}
    widget_names: List[str] = []

    # Declaration order is the order widgets serialise in
    for section in ("required", "optional"):
        entries = declared.get(section)
        if not isinstance(entries, dict):
            continue
        for input_name, spec in entries.items():
            if not _is_widget_spec(spec):
                continue
            widget_names.append(input_name)
            category = _category_from_choices(spec[0])
            if category:
                categories[input_name] = category
            if _adds_control_widget(spec):
                # Occupies a slot but is not an input the workflow names
                widget_names.append(None)

    return {"categories": categories, "widget_names": widget_names}


def get_node_schema(node_type: str) -> Optional[Dict[str, Any]]:
    """
    Declared widget layout and model categories for a node type, or None.

    Cached: a node class does not change while ComfyUI is running, and reading
    one means briefly swapping a module-level function.
    """
    if not node_type:
        return None
    if node_type in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[node_type]
    schema = _introspect(node_type)
    _SCHEMA_CACHE[node_type] = schema
    return schema


def get_model_widget_categories(node_type: str) -> Dict[str, str]:
    """Map each of a node's model widgets to the folder category it loads from."""
    schema = get_node_schema(node_type)
    return dict(schema["categories"]) if schema else {}


def get_widget_name(node_type: str, widget_index: Any) -> Optional[str]:
    """
    Name of the widget at a position in widgets_values.

    Dict-format widgets_values are already keyed by name, so those are returned
    unchanged. Returns None when the position cannot be identified.
    """
    if isinstance(widget_index, str):
        return widget_index
    if not isinstance(widget_index, int) or widget_index < 0:
        return None
    schema = get_node_schema(node_type)
    if not schema:
        return None
    names = schema["widget_names"]
    return names[widget_index] if widget_index < len(names) else None


def get_widget_category(node_type: str, widget_index: Any) -> Optional[str]:
    """
    Folder category for the widget at a position, if it holds a model.

    Falls back to the node's only model category when the position cannot be
    identified but the node has exactly one model widget - the common shape for
    a loader, where no ambiguity is possible.
    """
    categories = get_model_widget_categories(node_type)
    if not categories:
        return None

    name = get_widget_name(node_type, widget_index)
    if name and name in categories:
        return categories[name]

    if name is None and len(categories) == 1:
        return next(iter(categories.values()))
    return None


def clear_cache() -> None:
    """Forget cached schemas, for when node classes are reloaded."""
    _SCHEMA_CACHE.clear()
