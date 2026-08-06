"""Category aliasing: one folder of models reachable under several names."""

import unittest

from core.categories import canonical_category, categories_match


class CanonicalCategoryTests(unittest.TestCase):
    def test_diffusion_backbone_aliases_collapse(self):
        for alias in ('unet', 'unet_gguf', 'model_gguf', 'diffusion_models',
                      'select_safetensors', 'diffusion_models_gguf'):
            self.assertEqual(canonical_category(alias), 'diffusion_models', alias)

    def test_text_encoder_aliases_collapse(self):
        for alias in ('clip', 'clips', 'clip_gguf', 'text_encoder', 'text_encoders'):
            self.assertEqual(canonical_category(alias), 'text_encoders', alias)

    def test_unknown_values_become_none(self):
        # "no category known" has to stay distinguishable from a category that
        # simply has no alias, or every unknown would match every other unknown
        for value in (None, '', 'unknown', 'none', 'undefined', 'any'):
            self.assertIsNone(canonical_category(value), repr(value))

    def test_unlisted_names_pass_through(self):
        self.assertEqual(canonical_category('loras'), 'loras')
        self.assertEqual(canonical_category('some_pack_private_category'),
                         'some_pack_private_category')

    def test_case_and_whitespace_tolerated(self):
        self.assertEqual(canonical_category('  CLIP '), 'text_encoders')


class CategoriesMatchTests(unittest.TestCase):
    def test_aliases_match(self):
        self.assertTrue(categories_match('clip', 'text_encoders'))
        self.assertTrue(categories_match('unet', 'diffusion_models'))

    def test_unrelated_categories_do_not_match(self):
        self.assertFalse(categories_match('loras', 'vae'))

    def test_unknown_matches_nothing_including_itself(self):
        self.assertFalse(categories_match(None, 'loras'))
        self.assertFalse(categories_match('unknown', 'loras'))
        self.assertFalse(categories_match('unknown', 'unknown'))


if __name__ == '__main__':
    unittest.main()
