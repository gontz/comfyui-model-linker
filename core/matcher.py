"""
Fuzzy Matcher Module

Implements fuzzy string matching to find similar model names.

Plain edit distance ranks model filenames badly. "qwen3vl_8b_int8" differs from
"qwen3vl_4b_int8" by one character but is a different model, while it differs
from "qwen3vl_8b_bf16" by several characters and is the same model at another
precision. Scoring therefore works on a "family" name - the filename with
precision and quantisation tokens removed - alongside the raw comparison.

The family-name approach is modelled on the one in Comfyui-Model-Resolver
(Azornes, MIT), a descendant of this project; the implementation here is our own.
"""

import heapq
import os
import re
from functools import lru_cache
from typing import List, Dict, Optional, Tuple
from difflib import SequenceMatcher

from .categories import categories_match

try:
    from rapidfuzz import fuzz as _rapidfuzz
except ImportError:  # pragma: no cover - depends on the host environment
    _rapidfuzz = None


# A model library holds far fewer distinct filenames than there are comparisons
# to make: every missing model is scored against every candidate, and the same
# name recurs across categories. Caching keeps normalisation to once per name.
@lru_cache(maxsize=100_000)
def normalize_filename(filename: str) -> str:
    """
    Normalize a filename for comparison.

    Removes file extension, converts to lowercase, and normalizes
    separators (underscores, hyphens, dots, spaces).

    Args:
        filename: Filename to normalize

    Returns:
        Normalized string for comparison
    """
    # Remove file extension
    base = os.path.splitext(filename)[0]

    # Convert to lowercase
    base = base.lower()

    # Normalize separators: replace underscores, hyphens, dots and spaces with a
    # single space. Dots count because the same model is published as both
    # "flux1-dev.fp8" and "flux1_dev_fp8".
    base = re.sub(r'[_\-.\s]+', ' ', base)

    # Strip whitespace
    base = base.strip()

    return base


def calculate_similarity(str1: str, str2: str) -> float:
    """
    Calculate similarity score between two strings (0.0 to 1.0).

    Uses RapidFuzz when it is installed and SequenceMatcher otherwise. RapidFuzz
    is roughly 80x faster on a full-library comparison and agrees with
    SequenceMatcher on which candidate ranks first; it stays an optional import
    so the extension keeps working without it.

    Args:
        str1: First string
        str2: Second string

    Returns:
        Similarity score from 0.0 (completely different) to 1.0 (identical)
    """
    if _rapidfuzz is not None:
        return _rapidfuzz.ratio(str1, str2) / 100.0

    # Sequence order matters for SequenceMatcher: ratio() is not symmetric, so
    # callers must keep the target as the first argument.
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


# Tokens describing how a model was stored rather than which model it is.
# Removing them leaves a "family" name shared by every precision and
# quantisation variant of one model.
_VARIANT_TOKENS = {
    # floating point and integer precisions
    'fp32', 'fp16', 'bf16', 'fp8', 'fp4', 'nvfp4', 'int8', 'int4', 'nf4',
    'e4m3fn', 'e5m2', 'e4m3', 'f16', 'f32', 'mixed', 'fp8mixed',
    # quantisation schemes and toolchains
    'gguf', 'gptq', 'awq', 'quanto', 'scaled', 'convrot', 'quantized',
    # packaging variants
    'pruned', 'emaonly', 'ema', 'only', 'fixed', 'repackaged', 'distilled',
}

# Quantisation levels such as q8, q4_k_m, q6_k - matched by shape, since the
# exact vocabulary keeps growing.
_VARIANT_TOKEN_RE = re.compile(r'(?:q\d+(?:_?[a-z0-9]+)*|fp\d+|int\d+|nf\d+|e\d+m\d+)$')

# Tails that follow a quantisation head, e.g. the "0" and "k"/"m" in "q8 0" or
# "q4 k m" once separators have been normalised to spaces.
_VARIANT_TAIL_TOKENS = {'0', '1', 's', 'm', 'l', 'k', 'ks', 'km', 'kl'}
_VARIANT_HEAD_RE = re.compile(r'q\d+$')

# Parameter counts: 8b, 4b, 1.5b, 700m. These identify *which* model it is and
# must never be stripped.
_CAPACITY_TOKEN_RE = re.compile(r'\d+(?:\.\d+)?[bm]$')


@lru_cache(maxsize=100_000)
def normalize_model_family(filename: str) -> str:
    """
    Normalise a filename down to the model it names, ignoring how it was stored.

    "qwen3vl_8b_fp8_scaled" and "qwen3vl_8b_bf16" both reduce to "qwen3vl 8b":
    the same model, published at two precisions.
    """
    tokens = normalize_filename(filename).split()
    family = []
    after_quant_head = False

    for token in tokens:
        if token in _VARIANT_TOKENS or _VARIANT_TOKEN_RE.fullmatch(token):
            # A quantisation head like "q4" may be followed by "k"/"m"/"0"
            after_quant_head = bool(_VARIANT_HEAD_RE.fullmatch(token))
            continue
        if after_quant_head and token in _VARIANT_TAIL_TOKENS:
            continue
        after_quant_head = False
        family.append(token)

    return ' '.join(family)


@lru_cache(maxsize=100_000)
def get_model_signature(family_name: str) -> Optional[Tuple[str, str]]:
    """
    Split a family name into (architecture, parameter count) when it has one.

    "qwen3vl 8b" -> ("qwen3vl", "8b"). Returns None when the name carries no
    parameter count, which is normal for models like "umt5 xxl".
    """
    tokens = family_name.split()
    for index, token in enumerate(tokens):
        if not _CAPACITY_TOKEN_RE.fullmatch(token):
            continue
        architecture = ''.join(tokens[:index])
        if len(architecture) >= 3 and re.search(r'[a-z]', architecture):
            return architecture, token
    return None


def _architectures_differ_by_generation(left: str, right: str) -> bool:
    """
    Detect names that are identical apart from their digits.

    "qwen3vl" against "qwen2vl" is a different model generation, not a near miss.
    """
    left_digits = re.findall(r'\d+', left)
    right_digits = re.findall(r'\d+', right)
    if not left_digits or not right_digits or left_digits == right_digits:
        return False
    return re.sub(r'\d+', '', left) == re.sub(r'\d+', '', right)


# A match at or above this confidence is a plausible replacement rather than
# noise. Used to decide when a same-category match should outrank a better
# scoring one from a different category, and mirrors the UI's display threshold.
STRONG_MATCH_CONFIDENCE = 70.0

# Scores are banded so that the stronger signal always wins. Naming the same
# model matters more than looking alike: "flux_vae" and "flux1_vae_bf16" are
# nearly identical as strings, but only the first is the same model as
# "flux_vae_fp8". Each band leaves room for a small nudge from raw similarity so
# candidates within a band still order sensibly.
_TIEBREAK_RANGE = 0.019

# Same model, another precision or quantisation. A strong alternative, but held
# below an exact filename match so a real match always wins.
_SAME_FAMILY_SIMILARITY = 0.94

# Nothing whose family differs may reach the same-family band
_MAX_DIFFERENT_FAMILY_SIMILARITY = _SAME_FAMILY_SIMILARITY - 0.001

# Same architecture and size, different descriptive wording ("base" vs
# "instruct"). Probably usable, but the user should look.
_SAME_SIGNATURE_SIMILARITY = 0.85

# Same architecture, different parameter count or generation. A 4B model is not
# a stand-in for a missing 8B one however similar the names look, so this is
# pinned below the plausibility threshold instead of near the top.
_CAPACITY_CONFLICT_SIMILARITY = 0.69


def calculate_filename_confidence(target_filename: str, candidate_filename: str) -> float:
    """
    Score how well one filename replaces another, as a percentage.

    Combines raw name similarity with family awareness, so that variants of one
    model score high while different sizes of the same architecture do not.
    """
    target_norm = normalize_filename(target_filename)
    candidate_norm = normalize_filename(candidate_filename)

    if target_norm == candidate_norm:
        return 100.0

    similarity = calculate_similarity(target_norm, candidate_norm)

    target_family = normalize_model_family(target_filename)
    candidate_family = normalize_model_family(candidate_filename)
    if not target_family or not candidate_family:
        return round(min(similarity, 0.999) * 100, 1)

    target_signature = get_model_signature(target_family)
    candidate_signature = get_model_signature(candidate_family)

    # A differing parameter count or architecture generation means a different
    # model. Decided before any boost, and it caps rather than raises the score.
    if target_signature and candidate_signature:
        target_architecture, target_capacity = target_signature
        candidate_architecture, candidate_capacity = candidate_signature
        conflicting_size = target_capacity != candidate_capacity
        conflicting_generation = _architectures_differ_by_generation(
            target_architecture, candidate_architecture
        )
        if (conflicting_size and target_architecture == candidate_architecture) or conflicting_generation:
            return round(min(similarity, _CAPACITY_CONFLICT_SIMILARITY) * 100, 1)

    if target_family == candidate_family:
        # Identical once precision and quantisation are set aside. Raw
        # similarity only orders candidates within this band, so the closest
        # spelling of the same model comes first.
        similarity = _SAME_FAMILY_SIMILARITY + similarity * _TIEBREAK_RANGE
    else:
        similarity = max(similarity, calculate_similarity(target_family, candidate_family))
        if target_signature and target_signature == candidate_signature:
            # Same architecture and size; the names differ only in wording
            similarity = max(similarity, _SAME_SIGNATURE_SIMILARITY)
        # A different family never outranks the same model under another name,
        # however close the two strings happen to look.
        similarity = min(similarity, _MAX_DIFFERENT_FAMILY_SIMILARITY)

    # Never reach 100% without an exact normalised match
    return round(min(similarity, 0.999) * 100, 1)


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
    if max_results <= 0:
        return []

    # Extract just the filename from target_model (remove any subfolder paths)
    # target_model might be just a filename or might include subfolder paths
    target_filename = os.path.basename(target_model)

    # Normalize target filename once for exact match comparisons
    target_norm = normalize_filename(target_filename)

    # Only the best max_results are ever returned, so keep a heap of that size
    # rather than scoring everything into a list and sorting a whole library.
    # Entries are (rank_key, tiebreak, match); the smallest sits at heap[0] and
    # is the first to be displaced.
    heap: List[Tuple[Tuple[bool, float], int, Dict]] = []
    counter = 0

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

        # Normalised forms are memoised on the candidate itself. The scanner's
        # results are cached and reused across requests, so this is computed
        # once per model rather than once per model per missing reference.
        candidate_norm = candidate.get('_norm')
        if candidate_norm is None:
            candidate_norm = normalize_filename(candidate_filename)
            candidate['_norm'] = candidate_norm

        if target_norm == candidate_norm:
            # Exact match after normalization = 100% confidence
            similarity = 1.0
            confidence = 100.0
        else:
            confidence = calculate_filename_confidence(target_filename, candidate_filename)
            similarity = confidence / 100.0

        if similarity < threshold:
            continue

        match = {
            'model': candidate,
            'filename': candidate_filename,
            'similarity': similarity,
            'confidence': confidence,
            # Compared canonically, so a node expecting `text_encoders` still
            # counts a model catalogued under `clip` as its own category.
            'same_category': categories_match(preferred_category, candidate.get('category')),
        }
        # Plausible matches from the expected category outrank the rest; see the
        # preferred_category note above.
        rank = (
            match['same_category'] and confidence >= STRONG_MATCH_CONFIDENCE,
            similarity,
        )
        # Negate the counter so that among equal ranks the earlier candidate
        # compares larger, preserving the input order the caller established.
        entry = (rank, -counter, match)
        counter += 1

        if len(heap) < max_results:
            heapq.heappush(heap, entry)
        elif entry > heap[0]:
            heapq.heapreplace(heap, entry)

    return [entry[2] for entry in sorted(heap, reverse=True)]

