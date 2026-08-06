"""
Core modules for Model Linker extension.

Modules:
- linker: Main API for analyzing and resolving missing models
- scanner: Directory scanning for available models
- matcher: Fuzzy matching for finding similar models
- workflow_analyzer: Workflow JSON parsing
- workflow_updater: Workflow modification
- categories: Canonical category names for aliased model folders
- node_schema: Learns which folder each node widget loads from
- node_adapters: Readers for node packs storing references in their own shape
- overrides: Persisted user selections
- reveal: Opening a model's folder in the desktop file manager
"""

from .linker import analyze_and_find_matches, apply_resolution
from .scanner import get_model_files
from .matcher import find_matches

__all__ = [
    'analyze_and_find_matches',
    'apply_resolution',
    'get_model_files',
    'find_matches'
]
