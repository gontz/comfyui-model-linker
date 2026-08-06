"""
Custom Node Adapters

Most nodes keep a model reference as a plain string in `widgets_values`, which
the generic scan in workflow_analyzer reads directly. A few node packs use their
own shape, and this is where that knowledge lives - one adapter per pack, rather
than special cases spread through the analyzer and the updater.

Currently only ComfyUI-Lora-Manager needs one. Its loaders keep a list of
objects in a widget slot:

    ["<lora:some_lora:0.80>", [{"name": "some_lora", "strength": "0.80", ...}]]

Two things make that invisible to the generic scan: the reference sits inside a
list of objects rather than being a widget value itself, and `name` carries no
file extension, so nothing about it looks like a filename.

rgthree's Power Lora Loader needs no adapter - it stores objects directly in
widget slots (`{"on": true, "lora": "x.safetensors"}`), which the analyzer's
NESTED_MODEL_KEYS handling already reads.
"""

import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class NodeAdapter:
    """How to read and write the model references of one node pack."""

    adapter_id: str
    node_types: Tuple[str, ...]
    # (node) -> list of reference dicts, in the shape get_node_model_info returns
    extract: Callable[[Dict[str, Any]], List[Dict[str, Any]]]
    # (node, reference, new_value) -> whether the node was updated
    update: Callable[[Dict[str, Any], Dict[str, Any], str], bool]


# --- ComfyUI-Lora-Manager -------------------------------------------------

LORA_MANAGER_NODE_TYPES = (
    "Lora Loader (LoraManager)",
    "Lora Stacker (LoraManager)",
    "WanVideo Lora Select (LoraManager)",
    "LoraLoaderV2",
)


def _find_lora_list(widgets_values: Any) -> Optional[Tuple[int, List[Any]]]:
    """
    Locate the widget slot holding the list of lora entries.

    Found by shape rather than by a fixed index: the position differs between
    node types and has moved between releases of the pack.
    """
    if not isinstance(widgets_values, (list, tuple)):
        return None
    for index, value in enumerate(widgets_values):
        if not isinstance(value, list) or not value:
            continue
        if all(isinstance(item, dict) and isinstance(item.get("name"), str) for item in value):
            return index, value
    return None


def _lora_manager_extract(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Read the lora entries stored by a Lora Manager loader."""
    found = _find_lora_list(node.get("widgets_values"))
    if not found:
        return []

    widget_index, entries = found
    references = []
    for list_index, entry in enumerate(entries):
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        references.append({
            "node_id": node.get("id"),
            "node_type": node.get("type", ""),
            "widget_index": widget_index,
            "list_index": list_index,
            "nested_key": "name",
            "original_path": name,
            "category": "loras",
            # The pack stores names without a file extension and resolves them
            # against its own scan, so the linker has to match the same way.
            "extension_less": True,
            "full_path": None,
            "exists": False,
        })
    return references


def _strip_model_extension(value: str) -> str:
    """Drop a trailing model extension, leaving the name in the pack's format."""
    base, ext = os.path.splitext(value)
    return base if ext.lower() in {".safetensors", ".ckpt", ".pt", ".bin", ".pth", ".sft", ".gguf"} else value


def _lora_manager_update(node: Dict[str, Any], reference: Dict[str, Any], new_value: str) -> bool:
    """
    Point one lora entry at a different file.

    Written without an extension and with forward slashes, matching what the
    pack writes itself and what its own lookup expects.
    """
    widgets_values = node.get("widgets_values")
    widget_index = reference.get("widget_index")
    list_index = reference.get("list_index")
    if not isinstance(widgets_values, list):
        return False
    if not isinstance(widget_index, int) or not 0 <= widget_index < len(widgets_values):
        return False

    entries = widgets_values[widget_index]
    if not isinstance(entries, list):
        return False
    if not isinstance(list_index, int) or not 0 <= list_index < len(entries):
        return False
    entry = entries[list_index]
    if not isinstance(entry, dict):
        return False

    previous = entry.get("name") or ""
    written = _strip_model_extension(new_value).replace("\\", "/")
    entry["name"] = written

    # The companion text widget holds "<lora:name:strength>" tokens. The loader
    # itself reads the list, not the text, but the text is what the user sees
    # and what sibling text-driven nodes parse, so keep the two consistent.
    if previous and previous != written:
        for index, value in enumerate(widgets_values):
            if isinstance(value, str) and f"<lora:{previous}:" in value:
                widgets_values[index] = value.replace(
                    f"<lora:{previous}:", f"<lora:{written}:"
                )

    return True


LORA_MANAGER_ADAPTER = NodeAdapter(
    adapter_id="lora-manager",
    node_types=LORA_MANAGER_NODE_TYPES,
    extract=_lora_manager_extract,
    update=_lora_manager_update,
)


# --- Registry -------------------------------------------------------------

ADAPTERS: Tuple[NodeAdapter, ...] = (
    LORA_MANAGER_ADAPTER,
)

_BY_NODE_TYPE: Dict[str, NodeAdapter] = {
    node_type: adapter for adapter in ADAPTERS for node_type in adapter.node_types
}
_BY_ID: Dict[str, NodeAdapter] = {adapter.adapter_id: adapter for adapter in ADAPTERS}


def get_adapter(node_type: Optional[str]) -> Optional[NodeAdapter]:
    """Adapter registered for a node type, if the pack needs one."""
    return _BY_NODE_TYPE.get(node_type or "")


def get_adapter_by_id(adapter_id: Optional[str]) -> Optional[NodeAdapter]:
    """Adapter by its id, for applying a resolution recorded earlier."""
    return _BY_ID.get(adapter_id or "")
