"""
Banded, family-aware scoring.

The cases here are the ones that motivated the banding: plain edit distance
ranks model filenames backwards, preferring a different size of the same
architecture over the same model at another precision.
"""

import unittest

from core.matcher import (
    STRONG_MATCH_CONFIDENCE,
    calculate_filename_confidence,
    find_matches,
    get_model_signature,
    normalize_filename,
    normalize_model_family,
)


def candidates(*names, category='checkpoints'):
    return [{'filename': name, 'category': category, 'path': '/models/' + name}
            for name in names]


class NormalizationTests(unittest.TestCase):
    def test_separators_and_case_collapse(self):
        self.assertEqual(normalize_filename('FLUX1-Dev_FP8.safetensors'), 'flux1 dev fp8')

    def test_dots_are_separators_too(self):
        # The same model is published as both spellings
        self.assertEqual(normalize_filename('flux1-dev.fp8.safetensors'),
                         normalize_filename('flux1_dev_fp8.safetensors'))

    def test_family_drops_precision_and_quantisation(self):
        for name in ('qwen3vl_8b_fp8_scaled.safetensors', 'qwen3vl_8b_bf16.safetensors',
                     'qwen3vl-8b-Q4_K_M.gguf'):
            self.assertEqual(normalize_model_family(name), 'qwen3vl 8b', name)

    def test_family_keeps_parameter_count(self):
        self.assertIn('8b', normalize_model_family('qwen3vl_8b_int8.safetensors'))

    def test_signature_splits_architecture_and_capacity(self):
        self.assertEqual(get_model_signature('qwen3vl 8b'), ('qwen3vl', '8b'))

    def test_signature_absent_when_no_capacity_token(self):
        self.assertIsNone(get_model_signature('umt5 xxl'))


class ConfidenceBandTests(unittest.TestCase):
    def test_exact_after_normalization_is_100(self):
        self.assertEqual(
            calculate_filename_confidence('flux1-dev.safetensors', 'flux1_dev.safetensors'),
            100.0)

    def test_same_family_lands_in_its_band(self):
        score = calculate_filename_confidence('qwen3vl_8b_int8.safetensors',
                                              'qwen3vl_8b_bf16.safetensors')
        self.assertGreaterEqual(score, 94.0)
        self.assertLess(score, 96.0)

    def test_different_family_cannot_reach_the_same_family_band(self):
        # Nearly identical as strings, but a different model
        score = calculate_filename_confidence('flux_vae_fp8.safetensors',
                                              'flux1_vae_bf16.safetensors')
        self.assertLessEqual(score, 93.9)

    def test_same_model_outranks_a_closer_looking_different_one(self):
        target = 'flux_vae_fp8.safetensors'
        self.assertGreater(calculate_filename_confidence(target, 'flux_vae.safetensors'),
                           calculate_filename_confidence(target, 'flux1_vae_bf16.safetensors'))

    def test_capacity_conflict_is_pinned_below_plausibility(self):
        score = calculate_filename_confidence('qwen3vl_8b_int8.safetensors',
                                              'qwen3vl_4b_int8.safetensors')
        self.assertLessEqual(score, 69.0)
        self.assertLess(score, STRONG_MATCH_CONFIDENCE)

    def test_generation_conflict_is_pinned_below_plausibility(self):
        score = calculate_filename_confidence('qwen3vl_8b_int8.safetensors',
                                              'qwen2vl_8b_int8.safetensors')
        self.assertLessEqual(score, 69.0)

    def test_nothing_but_an_exact_match_reaches_100(self):
        self.assertLess(
            calculate_filename_confidence('flux1_dev.safetensors', 'flux1_schnell.safetensors'),
            100.0)


class FindMatchesTests(unittest.TestCase):
    def test_right_size_variants_rank_above_the_wrong_size(self):
        pool = candidates('qwen3vl_4b_int8.safetensors', 'qwen3vl_8b_bf16.safetensors',
                          'qwen3vl_8b_fp8_scaled.safetensors', 'umt5_xxl_fp16.safetensors')
        matches = find_matches('qwen3vl_8b_int8.safetensors', pool, max_results=4)
        plausible = [m['filename'] for m in matches if m['confidence'] >= STRONG_MATCH_CONFIDENCE]
        self.assertEqual(sorted(plausible),
                         ['qwen3vl_8b_bf16.safetensors', 'qwen3vl_8b_fp8_scaled.safetensors'])

    def test_threshold_excludes_weak_matches(self):
        pool = candidates('totally_unrelated_thing.safetensors')
        self.assertEqual(find_matches('flux1_dev.safetensors', pool, threshold=0.7), [])

    def test_zero_max_results_returns_nothing(self):
        self.assertEqual(find_matches('flux1_dev.safetensors', candidates('flux1_dev.safetensors'),
                                      max_results=0), [])

    def test_max_results_keeps_the_best_not_the_first(self):
        pool = candidates('unrelated_a.safetensors', 'unrelated_b.safetensors',
                          'flux1_dev.safetensors')
        matches = find_matches('flux1_dev.safetensors', pool, max_results=1)
        self.assertEqual([m['filename'] for m in matches], ['flux1_dev.safetensors'])

    def test_plausible_same_category_match_outranks_a_better_one_elsewhere(self):
        pool = (candidates('flux1_dev_fp8.safetensors', category='diffusion_models')
                + candidates('flux1_dev.safetensors', category='loras'))
        matches = find_matches('flux1_dev.safetensors', pool, max_results=2,
                               preferred_category='diffusion_models')
        self.assertEqual(matches[0]['filename'], 'flux1_dev_fp8.safetensors')
        self.assertTrue(matches[0]['same_category'])

    def test_category_preference_uses_aliases(self):
        # A model catalogued under `clip` belongs to a node loading from
        # `text_encoders`; comparing the names literally would say otherwise.
        pool = candidates('umt5_xxl_fp8.safetensors', category='clip')
        matches = find_matches('umt5_xxl_fp16.safetensors', pool,
                               preferred_category='text_encoders')
        self.assertTrue(matches[0]['same_category'])

    def test_weak_same_category_match_does_not_beat_a_strong_one_elsewhere(self):
        # A poor category guess has to stay recoverable
        pool = (candidates('nothing_alike_at_all.safetensors', category='vae')
                + candidates('flux1_dev_fp8.safetensors', category='diffusion_models'))
        matches = find_matches('flux1_dev.safetensors', pool, max_results=2,
                               preferred_category='vae')
        self.assertEqual(matches[0]['filename'], 'flux1_dev_fp8.safetensors')

    def test_candidates_without_a_filename_fall_back_to_the_path(self):
        pool = [{'path': '/models/checkpoints/flux1_dev.safetensors', 'category': 'checkpoints'}]
        matches = find_matches('flux1_dev.safetensors', pool)
        self.assertEqual(matches[0]['filename'], 'flux1_dev.safetensors')

    def test_target_subfolder_is_ignored_when_comparing(self):
        pool = candidates('flux1_dev.safetensors')
        matches = find_matches('flux/subdir/flux1_dev.safetensors', pool)
        self.assertEqual(matches[0]['confidence'], 100.0)


if __name__ == '__main__':
    unittest.main()
