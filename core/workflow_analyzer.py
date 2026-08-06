"""
Workflow Analyzer Module

Extracts model references from workflow JSON and identifies missing models.
"""

import os
import logging
from typing import List, Dict, Any, Optional

from .node_adapters import get_adapter
from .node_schema import get_model_widget_categories, get_widget_category

# Import folder_paths lazily - it may not be available until ComfyUI is initialized
try:
    import folder_paths
except ImportError:
    folder_paths = None
    logging.warning("Model Linker: folder_paths not available yet - will retry later")


# Common model file extensions
MODEL_EXTENSIONS = {'.ckpt', '.pt', '.pt2', '.bin', '.pth', '.safetensors', '.pkl', '.sft', '.onnx', '.gguf'}

# Node types that should never be scanned for model references.
# These are note/utility nodes whose widget values may contain model filenames
# as text content (e.g. markdown links) but are NOT actual model references.
SKIP_NODE_TYPES = {
    'MarkdownNote', 'Note', 'NoteNode', 'TextNote',
    'Reroute', 'PrimitiveNode',
}

# Mapping of common node types to their expected model category
# This is used as hints but we don't rely solely on this
# UNETLoader uses 'diffusion_models' category (folder_paths maps 'unet' to 'diffusion_models')
NODE_TYPE_TO_CATEGORY_HINTS = {
    'CheckpointLoaderSimple': 'checkpoints',
    'CheckpointLoader': 'checkpoints',
    'unCLIPCheckpointLoader': 'checkpoints',
    'VAELoader': 'vae',
    'LoraLoader': 'loras',
    'LoraLoaderModelOnly': 'loras',
    'UNETLoader': 'diffusion_models',  # UNETLoader uses diffusion_models category
    'ControlNetLoader': 'controlnet',
    'ControlNetLoaderAdvanced': 'controlnet',
    'CLIPLoader': 'text_encoders',
    'DualCLIPLoader': 'text_encoders',
    'TripleCLIPLoader': 'text_encoders',
    'CLIPVisionLoader': 'clip_vision',
    'UpscaleModelLoader': 'upscale_models',
    'LatentUpscaleModelLoader': 'latent_upscale_models',
    'StyleModelLoader': 'style_models',
    'HypernetworkLoader': 'hypernetworks',
    'EmbeddingLoader': 'embeddings',
    'Power Lora Loader (rgthree)': 'loras',
    # LTX-Video nodes
    'LTXVAudioVAELoader': 'checkpoints',
    'LowVRAMAudioVAELoader': 'checkpoints',
    'LTXVGemmaCLIPModelLoader': 'text_encoders',
}

# Keys within dict-type widget values that contain model file references.
# Some nodes (e.g. rgthree Power Lora Loader) store model info as objects like
# {"on": true, "lora": "name.safetensors", "strength": 1.0} inside widgets_values.
# Maps nested key name -> category hint.
NESTED_MODEL_KEYS = {
    'lora': 'loras',
    'ckpt_name': 'checkpoints',
    'checkpoint': 'checkpoints',
    'vae_name': 'vae',
    'control_net_name': 'controlnet',
}


def _basename(value: str) -> str:
    """Filename portion of a stored model reference, for either separator style."""
    return value.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]


def _lookup_model_metadata(model_metadata: Dict[str, Dict[str, Any]], value: str) -> Optional[Dict[str, Any]]:
    """Find the properties.models entry describing a stored widget value."""
    if not model_metadata or not isinstance(value, str):
        return None
    return model_metadata.get(value) or model_metadata.get(_basename(value))


def _source_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Pull the origin details a workflow records for a model it references.

    The download URL matters most when a model is missing and nothing on disk
    resembles it: fuzzy matching has nothing to offer, but the workflow still
    says where the file came from. The hash is recorded by newer frontends and
    identifies the file exactly, independent of what it was named.
    """
    if not metadata:
        return {'source_url': None, 'source_hash': None, 'source_hash_type': None}
    return {
        'source_url': metadata.get('url') or None,
        'source_hash': metadata.get('hash') or None,
        'source_hash_type': metadata.get('hash_type') or None,
    }


def is_model_filename(value: Any) -> bool:
    """
    Check if a value looks like a model filename.
    
    Args:
        value: The value to check
        
    Returns:
        True if it looks like a model filename
    """
    if not isinstance(value, str):
        return False
    
    # Check if it ends with a model extension
    _, ext = os.path.splitext(value.lower())
    return ext in MODEL_EXTENSIONS


def try_resolve_model_path(value: str, categories: List[str] = None) -> Optional[tuple[str, str]]:
    """
    Try to resolve a model path using folder_paths.
    
    Args:
        value: The model filename/path to resolve
        categories: Optional list of categories to try (if None, tries all)
        
    Returns:
        Tuple of (category, full_path) if found, None otherwise
    """
    if not isinstance(value, str) or not value.strip():
        return None
    
    # Remove any path separators that might indicate an absolute path prefix
    # Workflows should store relative paths, but handle both cases
    filename = value.strip()
    
    # Ensure folder_paths is available
    global folder_paths
    if folder_paths is None:
        try:
            import folder_paths as fp
            folder_paths = fp
        except ImportError:
            logging.error("Model Linker: folder_paths not available")
            return None
    
    # If categories not provided, try all categories
    if categories is None:
        categories = list(folder_paths.folder_names_and_paths.keys())
    
    # Skip non-model categories
    skip_categories = {'custom_nodes', 'configs'}
    categories = [c for c in categories if c not in skip_categories]
    
    for category in categories:
        try:
            full_path = folder_paths.get_full_path(category, filename)
            if full_path and os.path.exists(full_path):
                return (category, full_path)
        except Exception:
            continue
    
    return None


def resolve_model_reference(
    value: str,
    category: Optional[str],
    extension_less: bool = False
) -> Optional[tuple]:
    """
    Resolve a stored reference to a file on disk.

    Args:
        value: the reference as the workflow stores it
        category: folder category to search, when known
        extension_less: the reference omits the file extension, as the Lora
            Manager pack stores it. Such a value cannot be handed to
            folder_paths directly, so the extension is put back by matching
            against the category's listing.

    Returns:
        (category, full_path) when found, None otherwise.
    """
    if not extension_less:
        return try_resolve_model_path(value, [category] if category else None)

    global folder_paths
    if folder_paths is None:
        try:
            import folder_paths as fp
            folder_paths = fp
        except ImportError:
            return None

    wanted = value.strip().replace('\\', '/').lower()
    wanted_base = wanted.rsplit('/', 1)[-1]

    for candidate_category in ([category] if category else list(folder_paths.folder_names_and_paths.keys())):
        try:
            listing = folder_paths.get_filename_list(candidate_category)
        except Exception:
            continue
        for entry in listing:
            normalized = entry.replace('\\', '/').lower()
            stem = os.path.splitext(normalized)[0]
            # Accept either the full relative path or just the filename, which
            # is how the pack's own lookup behaves
            if stem == wanted or os.path.basename(stem) == wanted_base:
                try:
                    full_path = folder_paths.get_full_path(candidate_category, entry)
                except Exception:
                    continue
                if full_path and os.path.exists(full_path):
                    return (candidate_category, full_path)
    return None


def get_node_model_info(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract model references from a single node.

    This scans all widgets_values entries and tries to identify which ones
    are model file references by attempting to resolve them.

    Handles both array and dict formats for widgets_values (newer ComfyUI
    frontends may serialize widgets_values as an object with named keys).

    Uses properties.models (when available) for accurate category detection.

    Args:
        node: Node dictionary from workflow JSON

    Returns:
        List of model reference dictionaries:
        {
            'node_id': node id,
            'node_type': node type,
            'widget_index': index in widgets_values (int for array, str for dict),
            'original_path': original path from workflow,
            'category': model category (if found),
            'exists': True if model exists
        }
    """
    model_refs = []
    node_id = node.get('id')
    node_type = node.get('type', '')

    # Skip note/markdown/utility node types that don't contain model references
    if node_type in SKIP_NODE_TYPES:
        return model_refs

    widgets_values = node.get('widgets_values')
    if not widgets_values:
        return model_refs

    # Node packs that store references in their own shape are read by an adapter
    adapter = get_adapter(node_type)
    if adapter is not None:
        for ref in adapter.extract(node):
            ref['adapter_id'] = adapter.adapter_id
            resolved = resolve_model_reference(
                ref['original_path'], ref.get('category'), ref.get('extension_less', False)
            )
            if resolved:
                ref['category'], ref['full_path'] = resolved
                ref['exists'] = True
            model_refs.append(ref)
        return model_refs

    # Category hint for this node type. The hand-written table is only a
    # fallback now - node_schema asks the installed node class which folder each
    # widget loads from, which covers custom nodes the table has never seen.
    category_hint = NODE_TYPE_TO_CATEGORY_HINTS.get(node_type)
    schema_categories = get_model_widget_categories(node_type)
    if not category_hint and len(schema_categories) == 1:
        # A loader with a single model widget: unambiguous whatever the layout
        category_hint = next(iter(schema_categories.values()))

    # Build a lookup from properties.models, which recent frontends attach to
    # nodes that reference models. Entries look like:
    #   {"name": "flux1-dev.safetensors", "url": "https://...", "directory": "diffusion_models"}
    # and optionally carry "hash"/"hash_type".
    #
    # `name` is the model FILENAME - the value stored in the widget - not the
    # input name, so this is keyed by value and looked up per widget value below.
    # Entries are indexed by bare filename too, so a widget holding
    # "subfolder/model.safetensors" still finds its metadata.
    properties_models = (node.get('properties') or {}).get('models') or []
    model_metadata: Dict[str, Dict[str, Any]] = {}
    for pm in properties_models:
        if not isinstance(pm, dict):
            continue
        name = pm.get('name')
        if not isinstance(name, str) or not name:
            continue
        model_metadata[name] = pm
        model_metadata.setdefault(_basename(name), pm)

    # Handle both array and dict format for widgets_values
    if isinstance(widgets_values, dict):
        items = list(widgets_values.items())  # [(key, value), ...]
    elif isinstance(widgets_values, (list, tuple)):
        items = list(enumerate(widgets_values))  # [(index, value), ...]
    else:
        return model_refs

    for idx, value in items:
        # Case 1: Direct string value — detected either by file extension
        # or by properties.models declaring this exact value to be a model
        if isinstance(value, str) and value.strip():
            metadata = _lookup_model_metadata(model_metadata, value)
            declared_category = (metadata or {}).get('directory') or None

            # What the node class says this particular widget loads from. More
            # precise than the node-level hint on nodes with several model
            # widgets, e.g. DualCLIPLoader's clip_name1/clip_name2.
            widget_category = get_widget_category(node_type, idx)

            has_model_ext = is_model_filename(value)
            is_declared_model = metadata is not None or widget_category is not None

            if has_model_ext or is_declared_model:
                # Best category: workflow metadata > this widget's declared
                # folder > node type hint > search everything
                value_category = declared_category or widget_category or category_hint
                categories_to_try = [value_category] if value_category else None

                # Try to resolve the model path
                resolved = try_resolve_model_path(value, categories_to_try)

                if resolved:
                    category, full_path = resolved
                    exists = os.path.exists(full_path)
                else:
                    category = value_category or 'unknown'
                    full_path = None
                    exists = False

                model_refs.append({
                    'node_id': node_id,
                    'node_type': node_type,
                    'widget_index': idx,
                    'original_path': value,
                    'category': category,
                    'full_path': full_path,
                    'exists': exists,
                    'nested_key': None,
                    **_source_metadata(metadata)
                })
                continue

        # Case 2: Dict value containing a model reference key
        # e.g. {"on": true, "lora": "name.safetensors", "strength": 1.0}
        if isinstance(value, dict):
            for nested_key, nested_category_hint in NESTED_MODEL_KEYS.items():
                nested_value = value.get(nested_key)
                if not nested_value or not is_model_filename(nested_value):
                    continue

                metadata = _lookup_model_metadata(model_metadata, nested_value)
                value_category = (metadata or {}).get('directory') or nested_category_hint or category_hint
                categories_to_try = [value_category] if value_category else None

                resolved = try_resolve_model_path(nested_value, categories_to_try)

                if resolved:
                    category, full_path = resolved
                    exists = os.path.exists(full_path)
                else:
                    category = value_category or 'unknown'
                    full_path = None
                    exists = False

                model_refs.append({
                    'node_id': node_id,
                    'node_type': node_type,
                    'widget_index': idx,
                    'original_path': nested_value,
                    'category': category,
                    'full_path': full_path,
                    'exists': exists,
                    'nested_key': nested_key,
                    **_source_metadata(metadata)
                })

    return model_refs


def analyze_workflow_models(workflow_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract all model references from a workflow, including nested subgraphs.
    
    Args:
        workflow_json: Complete workflow JSON dictionary
        
    Returns:
        List of model reference dictionaries (same format as get_node_model_info)
        Each dict includes 'subgraph_id' if the model is in a subgraph
    """
    all_model_refs = []
    
    # Get subgraph definitions first to check if node types are subgraph UUIDs
    definitions = workflow_json.get('definitions', {})
    subgraphs = definitions.get('subgraphs', [])
    subgraph_lookup = {sg.get('id'): sg.get('name', sg.get('id')) for sg in subgraphs}
    
    # Analyze top-level nodes
    nodes = workflow_json.get('nodes', [])
    for node in nodes:
        try:
            model_refs = get_node_model_info(node)
            node_type = node.get('type', '')
            
            # Check if node type is a subgraph UUID
            subgraph_name = None
            subgraph_id = None
            if node_type in subgraph_lookup:
                subgraph_name = subgraph_lookup[node_type]
                subgraph_id = node_type
            
            # Mark with subgraph info if it's a subgraph node
            # For top-level subgraph instance nodes, subgraph_path is None
            # This distinguishes them from nodes within subgraph definitions
            for ref in model_refs:
                ref['subgraph_id'] = subgraph_id
                ref['subgraph_name'] = subgraph_name
                ref['subgraph_path'] = None  # Top-level, not in definitions.subgraphs
                ref['is_top_level'] = True  # Flag to indicate this is a top-level node
            all_model_refs.extend(model_refs)
        except Exception as e:
            logging.warning(f"Error analyzing node {node.get('id', 'unknown')}: {e}")
            continue
    
    # Recursively analyze subgraphs (definitions already loaded above)
    if not subgraphs:  # Re-get if not loaded above
        subgraphs = definitions.get('subgraphs', [])
    
    for subgraph in subgraphs:
        subgraph_id = subgraph.get('id')
        subgraph_name = subgraph.get('name', subgraph_id)
        subgraph_nodes = subgraph.get('nodes', [])
        
        logging.debug(f"Analyzing subgraph: {subgraph_name} (ID: {subgraph_id}) with {len(subgraph_nodes)} nodes")
        
        for node in subgraph_nodes:
            try:
                model_refs = get_node_model_info(node)
                # Mark as belonging to this subgraph definition
                for ref in model_refs:
                    ref['subgraph_id'] = subgraph_id
                    ref['subgraph_name'] = subgraph_name
                    ref['subgraph_path'] = ['definitions', 'subgraphs', subgraph_id, 'nodes']
                    ref['is_top_level'] = False  # This is inside a subgraph definition
                all_model_refs.extend(model_refs)
            except Exception as e:
                logging.warning(f"Error analyzing subgraph node {node.get('id', 'unknown')}: {e}")
                continue
    
    return all_model_refs


def identify_missing_models(
    workflow_models: List[Dict[str, Any]],
    available_models: List[Dict[str, str]] = None
) -> List[Dict[str, Any]]:
    """
    Identify which models from the workflow are missing.
    Deduplicates by filename - same model file only appears once even if
    referenced by multiple nodes.
    
    Args:
        workflow_models: List of model references from analyze_workflow_models
        available_models: Optional list of available models (if None, checks via folder_paths)
        
    Returns:
        List of missing model references (deduplicated by filename).
        Each entry has 'all_node_refs' containing all node references for that model.
    """
    # Group missing models by filename to deduplicate
    missing_by_filename: Dict[str, Dict[str, Any]] = {}
    
    for model_ref in workflow_models:
        # If exists is False, it's missing
        if not model_ref.get('exists', False):
            filename = model_ref.get('original_path', '')
            
            if filename not in missing_by_filename:
                # First occurrence - use this as the primary entry
                missing_by_filename[filename] = {
                    **model_ref,
                    'all_node_refs': [model_ref.copy()]  # Track all nodes needing this model
                }
            else:
                # Duplicate - just add to the node refs list
                missing_by_filename[filename]['all_node_refs'].append(model_ref.copy())
    
    # Return deduplicated list
    return list(missing_by_filename.values())

