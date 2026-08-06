"""
Fuzzy Matcher Module

Implements fuzzy string matching to find similar model names.
"""

import os
import re
from functools import lru_cache
from typing import List, Dict, Tuple
from difflib import SequenceMatcher


# A model library holds far fewer distinct filenames than there are comparisons
# to make: every missing model is scored against every candidate, and the same
# name recurs across categories. Caching keeps normalisation to once per name.
@lru_cache(maxsize=100_000)
def normalize_filename(filename: str) -> str:
    """
    Normalize a filename for comparison.
    
    Removes file extension, converts to lowercase, and normalizes
    separators (underscores, hyphens, spaces).
    
    Args:
        filename: Filename to normalize
        
    Returns:
        Normalized string for comparison
    """
    # Remove file extension
    base = os.path.splitext(filename)[0]
    
    # Convert to lowercase
    base = base.lower()
    
    # Normalize separators: replace underscores, hyphens, and spaces with a single space
    base = re.sub(r'[_\-\s]+', ' ', base)
    
    # Strip whitespace
    base = base.strip()
    
    return base


def calculate_similarity(str1: str, str2: str) -> float:
    """
    Calculate similarity score between two strings (0.0 to 1.0).
    
    Uses SequenceMatcher to compute a ratio.
    
    Args:
        str1: First string
        str2: Second string
        
    Returns:
        Similarity score from 0.0 (completely different) to 1.0 (identical)
    """
    return SequenceMatcher(None, str1, str2).ratio()


def calculate_similarity_with_normalization(str1: str, str2: str) -> float:
    """
    Calculate similarity score with filename normalization.
    
    Normalizes both strings before comparing.
    
    Args:
        str1: First string (typically model filename)
        str2: Second string (typically candidate model filename)
        
    Returns:
        Similarity score from 0.0 to 1.0
    """
    norm1 = normalize_filename(str1)
    norm2 = normalize_filename(str2)
    return calculate_similarity(norm1, norm2)


# A match at or above this confidence is a plausible replacement rather than
# noise. Used to decide when a same-category match should outrank a better
# scoring one from a different category, and mirrors the UI's display threshold.
STRONG_MATCH_CONFIDENCE = 70.0


def find_matches(
    target_model: str,
    candidate_models: List[Dict[str, str]],
    threshold: float = 0.0,
    max_results: int = 10,
    preferred_category: str = None
) -> List[Dict[str, any]]:
    """
    Find similar models using fuzzy matching.

    Args:
        target_model: The target model filename/path to match
        candidate_models: List of candidate model dictionaries with 'filename' or 'path' key
        threshold: Minimum similarity score (0.0 to 1.0) to include in results
        max_results: Maximum number of results to return
        preferred_category: Category the node expects, if known. Plausible
            matches from this category are ranked above better-scoring ones from
            other categories, because a path only resolves against the folder
            its own category points at - suggesting a VAE for a missing
            checkpoint produces a workflow that still fails to load. Weak
            same-category matches get no such boost, so a genuinely better match
            elsewhere still surfaces when the category is a poor guide.

    Returns:
        List of match dictionaries sorted by similarity (highest first):
        {
            'model': original model dict from candidates,
            'filename': model filename,
            'similarity': similarity score (0.0 to 1.0),
            'confidence': confidence percentage (0 to 100),
            'same_category': whether the match came from preferred_category
        }
    """
    matches = []

    # Extract just the filename from target_model (remove any subfolder paths)
    # target_model might be just a filename or might include subfolder paths
    target_filename = os.path.basename(target_model)

    # Normalize target filename once for exact match comparisons
    target_norm = normalize_filename(target_filename)

    # normalize_filename already drops one extension, so normalizing the
    # extension-stripped name too only differs for names carrying more than one
    # dot ("model.v1.2.safetensors"). Both forms are compared, as before.
    target_base_norm = normalize_filename(os.path.splitext(target_filename)[0])

    for candidate in candidate_models:
        # Get filename from candidate (prefer 'filename' key, fallback to extracting from 'path' or 'relative_path')
        candidate_filename = candidate.get('filename')
        
        # If no filename key, try to extract from path or relative_path
        if not candidate_filename:
            candidate_path = candidate.get('path', '') or candidate.get('relative_path', '')
            if candidate_path:
                candidate_filename = os.path.basename(candidate_path)
        
        if not candidate_filename:
            continue
        
        # Calculate similarity comparing just filenames (not paths)
        # This ensures we're comparing apples to apples
        
        # First check for exact match (after normalization) - should be 100%
        # Only exact matches should get 100% confidence
        candidate_norm = normalize_filename(candidate_filename)
        
        if target_norm == candidate_norm:
            # Exact match after normalization = 100% confidence
            similarity = 1.0
        else:
            # Calculate similarity using SequenceMatcher
            # This gives a ratio between 0.0 and 1.0 based on longest common subsequence
            # Sequence order matters here: SequenceMatcher.ratio() is not
            # symmetric, so the target must stay the first argument.
            similarity = calculate_similarity(target_norm, candidate_norm)

            # Also try comparing without extensions for better matching
            candidate_base_norm = normalize_filename(os.path.splitext(candidate_filename)[0])
            similarity_no_ext = calculate_similarity(target_base_norm, candidate_base_norm)

            # Use the higher of the two similarity scores
            # But ensure we never get 1.0 unless it's an exact normalized match
            similarity = max(similarity, similarity_no_ext)

            # Cap similarity at 0.999 for non-exact matches to prevent false 100% scores
            # SequenceMatcher can sometimes give 1.0 for very similar but not identical strings
            # due to normalization artifacts
            # (this branch only runs when target_norm != candidate_norm)
            if similarity >= 0.999:
                similarity = 0.999
        
        # Only include if above threshold
        if similarity >= threshold:
            confidence = round(similarity * 100, 1)  # Convert to percentage
            matches.append({
                'model': candidate,
                'filename': candidate_filename,
                'similarity': similarity,
                'confidence': confidence,
                'same_category': bool(
                    preferred_category and candidate.get('category') == preferred_category
                )
            })

    # Sort by similarity (highest first), with plausible matches from the
    # expected category ahead of the rest. Without a preferred_category this
    # reduces to ordering by similarity alone.
    matches.sort(key=lambda x: (
        x['same_category'] and x['confidence'] >= STRONG_MATCH_CONFIDENCE,
        x['similarity'],
    ), reverse=True)

    # Limit to max_results
    matches = matches[:max_results]

    return matches

