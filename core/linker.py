"""
Core Linker Module

Integrates all components to provide high-level API for model linking.
"""

import os
import logging
from typing import Dict, Any, List, Optional

from .scanner import get_model_files
from .workflow_analyzer import analyze_workflow_models, identify_missing_models
from .matcher import find_matches
from .workflow_updater import update_workflow_nodes
from .overrides import find_override_model


def physical_file_key(model: Dict[str, Any]) -> str:
    """
    Identity of the physical file a catalogued model entry points at.

    Uses the scanner's resolved `real_path` so entries reached through
    junctioned/symlinked category directories collapse together. Falls back to
    `path` for entries from older callers that predate `real_path`.
    """
    path = model.get('real_path') or model.get('path') or ''
    if not path:
        return ''
    try:
        return os.path.normcase(os.path.normpath(path))
    except Exception:
        return path


def group_models_by_physical_file(models: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Group catalogued models by the physical file behind them.

    One file is catalogued once per category whose directory reaches it, and
    those directories are often links to a single shared folder - so the same
    model can appear a dozen times under different category names and paths.
    Grouping here lets each physical file be scored and shown exactly once.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for model in models:
        groups.setdefault(physical_file_key(model), []).append(model)
    return groups


def select_candidates(groups: Dict[str, List[Dict[str, Any]]], category: Optional[str]) -> List[Dict[str, Any]]:
    """
    Pick one candidate per physical file for a given target category.

    Where a file is catalogued under several categories, prefer the entry whose
    category matches the one the node expects, so the relative path written back
    into the workflow resolves against that loader's folder.

    Entries in the preferred category are returned first. find_matches sorts by
    score with a stable sort, so candidates that score equally keep this order.
    """
    preferred: List[Dict[str, Any]] = []
    others: List[Dict[str, Any]] = []

    want_category = category if category and category != 'unknown' else None

    for entries in groups.values():
        chosen = None
        if want_category:
            for entry in entries:
                if entry.get('category') == want_category:
                    chosen = entry
                    break
        if chosen is not None:
            preferred.append(chosen)
        else:
            others.append(entries[0])

    return preferred + others


def list_available_models() -> List[Dict[str, Any]]:
    """
    Catalogue for the UI's model picker.

    Entries keep their category: the category decides which folder the written
    path is resolved against, so a file reachable under several categories stays
    listed once per category rather than being collapsed to one row. Only exact
    duplicates within a single category are dropped - the same physical file
    reached through two directories configured for that category.

    Each entry carries a `file_id` shared by every row pointing at the same
    physical file. Where whole category folders are links to one directory, the
    same model is catalogued under each of them, sometimes with a different
    relative path depending on which root it was reached from - so the id is the
    only reliable way for the picker to tell those rows apart from genuinely
    different files. The resolved path itself is stripped, being both large and
    of no use to the UI.
    """
    seen = set()
    file_ids: Dict[str, int] = {}
    listing: List[Dict[str, Any]] = []

    for model in get_model_files():
        file_key = physical_file_key(model)
        key = (file_key, model.get('category'))
        if key in seen:
            continue
        seen.add(key)

        entry = {k: v for k, v in model.items() if k != 'real_path'}
        entry['file_id'] = file_ids.setdefault(file_key, len(file_ids))
        listing.append(entry)

    return listing


def analyze_and_find_matches(
    workflow_json: Dict[str, Any],
    similarity_threshold: float = 0.0,
    max_matches_per_model: int = 10
) -> Dict[str, Any]:
    """
    Main entry point: analyze workflow and find matches for missing models.
    
    Args:
        workflow_json: Complete workflow JSON dictionary
        similarity_threshold: Minimum similarity score (0.0 to 1.0) for matches
        max_matches_per_model: Maximum number of matches to return per missing model
        
    Returns:
        Dictionary with analysis results:
        {
            'missing_models': [
                {
                    'node_id': node ID,
                    'node_type': node type,
                    'widget_index': widget index,
                    'original_path': original path from workflow,
                    'category': model category,
                    'matches': [
                        {
                            'model': model dict from scanner,
                            'filename': model filename,
                            'similarity': similarity score (0.0-1.0),
                            'confidence': confidence percentage (0-100)
                        },
                        ...
                    ]
                },
                ...
            ],
            'total_missing': count of missing models,
            'total_models_analyzed': count of all models in workflow
        }
    """
    # Analyze workflow to find all model references
    all_model_refs = analyze_workflow_models(workflow_json)
    
    # Get available models
    available_models = get_model_files()

    # Collapse link aliases up front so each physical file is scored once
    # instead of once per category directory that happens to reach it.
    model_groups = group_models_by_physical_file(available_models)

    # Identify missing models
    missing_models = identify_missing_models(all_model_refs, available_models)

    # Find matches for each missing model
    missing_with_matches = []
    for missing in missing_models:
        original_path = missing.get('original_path', '')
        
        # Filter available models by category if known
        # IMPORTANT: If category is 'unknown', we still try to find the right category
        # by using node type hints
        category = missing.get('category')
        
        # If category is unknown, try to use node type to infer category
        if not category or category == 'unknown':
            from .workflow_analyzer import NODE_TYPE_TO_CATEGORY_HINTS
            node_type = missing.get('node_type', '')
            category = NODE_TYPE_TO_CATEGORY_HINTS.get(node_type, 'unknown')
        
        # One candidate per physical file, entries in the expected category first
        candidates = select_candidates(model_groups, category)

        # First: compute fuzzy matches as usual
        matches = find_matches(
            original_path,
            candidates,
            threshold=similarity_threshold,
            max_results=max_matches_per_model,
            preferred_category=category if category != 'unknown' else None
        )

        # Then: check if user has a saved override; inject it as a 99% match (not 100%)
        override_model = find_override_model(original_path, category, available_models)
        if override_model is not None:
            override_key = physical_file_key(override_model)
            # Check if already present in matches
            found = None
            for m in matches:
                key = physical_file_key(m.get('model', {}))
                if key and key == override_key:
                    found = m
                    break
            if found:
                # Boost to 100% and mark as override
                found['similarity'] = 1.0
                found['confidence'] = 100.0
                found['is_override'] = True
            else:
                matches.append({
                    'model': override_model,
                    'filename': override_model.get('filename'),
                    'similarity': 1.0,
                    'confidence': 100.0,
                    'is_override': True,
                })
        
        # Safety net: the candidate pool is already one entry per physical file,
        # but an injected override can collide with a match, so collapse again on
        # the same physical-file identity and keep the higher-confidence entry.
        seen_files = {}
        deduplicated_matches = []
        for match in matches:
            file_key = physical_file_key(match['model'])

            if file_key not in seen_files:
                seen_files[file_key] = match
                deduplicated_matches.append(match)
            else:
                existing_match = seen_files[file_key]
                if match['confidence'] > existing_match['confidence']:
                    idx = deduplicated_matches.index(existing_match)
                    deduplicated_matches[idx] = match
                    seen_files[file_key] = match

        missing_with_matches.append({
            **missing,
            'matches': deduplicated_matches
        })
    
    return {
        'missing_models': missing_with_matches,
        'total_missing': len(missing_with_matches),
        'total_models_analyzed': len(all_model_refs)
    }


def apply_resolution(
    workflow_json: Dict[str, Any],
    resolutions: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Apply model resolutions to workflow.
    
    Args:
        workflow_json: Workflow JSON dictionary (will be modified)
        resolutions: List of resolution dictionaries:
            {
                'node_id': node ID,
                'widget_index': widget index,
                'resolved_path': absolute path to resolved model,
                'category': model category (optional),
                'resolved_model': model dict from scanner (optional)
            }
            
    Returns:
        Updated workflow JSON dictionary
    """
    # Prepare mappings for workflow_updater
    mappings = []
    for resolution in resolutions:
        mapping = {
            'node_id': resolution.get('node_id'),
            'widget_index': resolution.get('widget_index'),
            'resolved_path': resolution.get('resolved_path'),
            'category': resolution.get('category'),
            'resolved_model': resolution.get('resolved_model'),
            'subgraph_id': resolution.get('subgraph_id'),  # Include subgraph_id for subgraph nodes
            'is_top_level': resolution.get('is_top_level'),  # True for top-level nodes, False for nodes in subgraph definitions
            'nested_key': resolution.get('nested_key'),  # For dict-type widgets (e.g. Power Lora Loader)
        }
        
        # If resolved_model provided, extract path if needed
        if 'resolved_model' in resolution and resolution['resolved_model']:
            resolved_model = resolution['resolved_model']
            if 'path' in resolved_model and not mapping.get('resolved_path'):
                mapping['resolved_path'] = resolved_model['path']
            if 'base_directory' in resolved_model:
                mapping['base_directory'] = resolved_model['base_directory']
        
        mappings.append(mapping)
    
    # Update workflow
    updated_workflow = update_workflow_nodes(workflow_json, mappings)
    
    return updated_workflow


def get_resolution_summary(workflow_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get summary of missing models and matches without applying resolutions.
    
    This is a convenience method that calls analyze_and_find_matches with defaults.
    
    Args:
        workflow_json: Complete workflow JSON dictionary
        
    Returns:
        Same format as analyze_and_find_matches
    """
    return analyze_and_find_matches(workflow_json)

