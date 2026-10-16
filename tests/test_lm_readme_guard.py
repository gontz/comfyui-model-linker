"""
Lora Manager 1.2.5 freezes ComfyUI on some HuggingFace model cards (an infinite
loop in its README converter). Model Linker checks a card before asking Lora
Manager to read it. See docs/lora-manager-readme-hang.md.
"""

import unittest
from unittest import mock

try:
    import requests
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import lm_readme_guard as guard

MRADERMACHER_LINE = '***For a convenient overview and download list, visit our [model page](https://hf.tst/x)***'


class WouldHangTests(unittest.TestCase):
    def test_shapes_that_hang_lora_manager(self):
        # Each confirmed against Lora Manager 1.2.5's own converter
        for text in ('***x***', MRADERMACHER_LINE, '--- text', '----x', '# ', '#### ', '#\t',
                     '| a |', '>\tquote', '> ***x***', 'para\n---</p>'):
            with self.subTest(text=text):
                self.assertTrue(guard.would_hang(text))

    def test_shapes_it_reads_fine(self):
        for text in ('***', '---', '# x', '> q', '**b**', '- item', '1. step', 'plain text',
                     '| a | b |\n|---|---|\n| 1 | 2 |', '```\n***x***\n```', '<!-- ***x*** -->'):
            with self.subTest(text=text):
                self.assertFalse(guard.would_hang(text))

    def test_frontmatter_is_skipped_like_lora_manager_does(self):
        self.assertFalse(guard.would_hang('---\nlicense: mit\ntags:\n- x\n---\n# Title\ntext'))


class Response:
    def __init__(self, status, text=''):
        self.status_code, self.text = status, text


class CardIsSafeTests(unittest.TestCase):
    def setUp(self):
        guard.clear_cache()
        self.addCleanup(guard.clear_cache)
        self.urls = []

    def respond(self, *responses):
        queue = list(responses)

        def get(url, **kwargs):
            self.urls.append(url)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return mock.patch.object(requests, 'get', side_effect=get)

    def test_a_card_that_would_hang_is_unsafe(self):
        with self.respond(Response(200, f'# Model\n{MRADERMACHER_LINE}\n')):
            self.assertIs(guard.card_is_safe('mradermacher/X-GGUF'), False)
        self.assertEqual(self.urls, ['https://huggingface.co/mradermacher/X-GGUF/raw/main/README.md'])

    def test_a_readable_card_is_safe_and_the_answer_is_cached(self):
        with self.respond(Response(200, '# Model\nFine text.')):
            self.assertIs(guard.card_is_safe('o/r'), True)
            self.assertIs(guard.card_is_safe('o/r'), True)
        self.assertEqual(len(self.urls), 1)

    def test_master_is_tried_after_main_like_lora_manager(self):
        with self.respond(Response(404), Response(200, '***x***')):
            self.assertIs(guard.card_is_safe('o/r'), False)
        self.assertTrue(self.urls[1].endswith('/raw/master/README.md'))

    def test_no_readme_at_all_is_safe(self):
        with self.respond(Response(404), Response(404)):
            self.assertIs(guard.card_is_safe('o/r'), True)

    def test_a_failed_fetch_is_unknown_and_not_cached(self):
        with self.respond(requests.ConnectionError('down'), Response(200, 'ok')):
            self.assertIsNone(guard.card_is_safe('o/r'))
            self.assertIs(guard.card_is_safe('o/r'), True)

    def test_a_server_error_is_unknown(self):
        with self.respond(Response(503)):
            self.assertIsNone(guard.card_is_safe('o/r'))

    def test_the_documented_check_matches_the_shipped_one(self):
        # docs/lora-manager-readme-hang.md carries a copy for Lora Manager's maintainers
        import os
        doc = os.path.join(os.path.dirname(__file__), '..', 'docs', 'lora-manager-readme-hang.md')
        with open(doc, encoding='utf-8') as handle:
            text = handle.read()
        for pattern in (guard._BREAK.pattern, guard._HANDLED.pattern, guard._TABLE_SEP.pattern):
            self.assertIn(pattern, text)


if __name__ == '__main__':
    unittest.main()
