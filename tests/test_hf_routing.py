"""Which engine takes a URL - decided by the real host."""

import unittest
from unittest import mock

try:
    import requests  # noqa: F401
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import downloader
from support import LibraryTestCase


class RoutingTests(unittest.TestCase):
    def test_lookalike_host_streams(self):
        self.assertIsNone(downloader._hf_engine_for(
            'https://huggingface.co.evil.example/a/b/resolve/main/c.safetensors'))

    def test_civitai_streams(self):
        self.assertIsNone(downloader._hf_engine_for('https://civitai.com/api/download/models/1'))

    def test_real_huggingface_uses_the_engine_when_available(self):
        try:
            import core.hf_downloader  # noqa: F401
        except ImportError:
            self.skipTest('huggingface_hub >= 1.1 is not installed')
        engine = downloader._hf_engine_for('https://huggingface.co/Comfy-Org/x/resolve/main/sub/c.safetensors')
        self.assertIsNotNone(engine)
        self.assertEqual(engine[1]['filename'], 'sub/c.safetensors')

    def test_missing_library_falls_back(self):
        # Even after the engine was imported once in this process - the case
        # a plain `from . import hf_downloader` gets wrong
        try:
            import core.hf_downloader  # noqa: F401
        except ImportError:
            pass
        with mock.patch.dict('sys.modules', {'core.hf_downloader': None}):
            self.assertIsNone(downloader._hf_engine_for(
                'https://huggingface.co/Comfy-Org/x/resolve/main/c.safetensors'))


class RecordTests(LibraryTestCase):
    """The engine shares downloader's progress record and must not drop its fields."""

    def setUp(self):
        super().setUp()
        try:
            from core import hf_downloader
        except ImportError:
            self.skipTest('huggingface_hub >= 1.1 is not installed')
        self.hf = hf_downloader
        self.loras = self.add_models('loras', [], ['.safetensors'])
        for patcher in (mock.patch.object(downloader, '_ensure_workers'),
                        mock.patch.object(hf_downloader, 'get_token', return_value=None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_duplicate_guard_holds_while_the_engine_runs(self):
        url = 'https://huggingface.co/o/r/resolve/main/x.safetensors'
        download_id = downloader.start_download(url, 'loras', 'x.safetensors')
        # _ensure_workers is mocked, so nothing ever drains the queue entry
        # this created; without this, a later test's real worker could pop it
        # and retry a download against this test's already-removed temp dir.
        self.addCleanup(downloader._jobs.pop, download_id, None)
        refused = []

        def popen(*args, **kwargs):
            # The engine has written its 'starting' record by now
            try:
                downloader.start_download(url, 'loras', 'x.safetensors')
            except downloader.DownloadError:
                refused.append(True)
            raise OSError('no worker process in tests')

        with mock.patch.object(self.hf.subprocess, 'Popen', popen):
            self.hf.download_hf_model('o/r', 'x.safetensors', self.loras, 'x.safetensors', download_id)
        self.assertEqual(refused, [True])
        record = downloader.get_progress(download_id)
        self.assertEqual(record['status'], 'error')
        self.assertEqual(record['category'], 'loras')
        self.assertTrue(record['path'].endswith('x.safetensors'))
