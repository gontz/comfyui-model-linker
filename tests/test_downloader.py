"""
Downloads: where files may land, what is sent where, and that nothing
model-named is left behind by a failure.
"""

import hashlib
import os
import threading
import unittest
from unittest import mock

try:
    import requests
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import downloader, overrides, settings
from support import LibraryTestCase

BODY = b'model weights' * 1000


class FakeResponse:
    def __init__(self, body=BODY, status_code=200, length=None):
        self.body = body
        self.status_code = status_code
        self.headers = {'content-length': str(len(body) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        for i in range(0, len(self.body), 4096):
            yield self.body[i:i + 4096]


class DownloaderTestCase(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.loras = self.add_models('loras', [], ['.safetensors'])
        self.sent = []
        # Tokens are read from settings.json; never the developer's real one
        data_dir = os.path.join(self.root, 'data')
        os.makedirs(data_dir, exist_ok=True)
        for patcher in (mock.patch.object(downloader, '_ensure_workers'),
                        mock.patch.object(overrides, '_data_dir', lambda: data_dir)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def respond(self, response):
        def fake_get(url, headers=None, **kwargs):
            self.sent.append((url, dict(headers or {})))
            return response
        patcher = mock.patch.object(requests, 'get', fake_get)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_download(self, url='https://civitai.com/api/download/models/1', filename='new.safetensors', **kw):
        download_id = downloader.start_download(url, 'loras', filename, **kw)
        downloader._run(download_id)
        return downloader.get_progress(download_id)


class DestinationTests(DownloaderTestCase):
    def test_only_https(self):
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('http://civitai.com/x', 'loras', 'a.safetensors')

    def test_folders_in_the_name_are_dropped(self):
        self.respond(FakeResponse())
        record = self.run_download(filename='..\\..\\escape.safetensors')
        self.assertEqual(record['status'], 'completed')
        self.assertTrue(os.path.isfile(os.path.join(self.loras, 'escape.safetensors')))

    def test_rejects_drive_relative_and_hidden_names(self):
        # On Windows basename('C:evil.safetensors') is 'evil.safetensors', so
        # the colon must be refused in the name as given
        for name in ('C:evil.safetensors', 'a:b.safetensors', '.hidden.safetensors', '', 'x.exe'):
            with self.assertRaises(downloader.DownloadError, msg=name):
                downloader.start_download('https://civitai.com/x', 'loras', name)

    def test_rejects_unknown_category(self):
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('https://civitai.com/x', 'no_such_category', 'a.safetensors')

    def test_never_overwrites(self):
        with open(os.path.join(self.loras, 'have.safetensors'), 'wb') as handle:
            handle.write(b'x')
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('https://civitai.com/x', 'loras', 'have.safetensors')

    def test_refuses_a_second_download_of_the_same_file(self):
        downloader.start_download('https://civitai.com/x', 'loras', 'twice.safetensors')
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('https://civitai.com/y', 'loras', 'twice.safetensors')


class TransferTests(DownloaderTestCase):
    def test_streams_then_renames_and_hashes(self):
        self.respond(FakeResponse())
        record = self.run_download()
        path = os.path.join(self.loras, 'new.safetensors')
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['sha256'], hashlib.sha256(BODY).hexdigest())
        with open(path, 'rb') as handle:
            self.assertEqual(handle.read(), BODY)
        self.assertFalse(os.path.exists(path + '.part'))

    def test_short_body_leaves_nothing(self):
        self.respond(FakeResponse(length=len(BODY) * 4))
        record = self.run_download()
        self.assertEqual(record['status'], 'error')
        self.assertEqual(os.listdir(self.loras), [])

    def test_file_appearing_mid_download_is_not_overwritten(self):
        dest = os.path.join(self.loras, 'new.safetensors')

        class Racing(FakeResponse):
            def iter_content(self, size):
                for i, chunk in enumerate(super().iter_content(size)):
                    if i == 1:
                        with open(dest, 'wb') as handle:
                            handle.write(b'theirs')
                    yield chunk

        self.respond(Racing())
        record = self.run_download()
        self.assertEqual(record['status'], 'error')
        with open(dest, 'rb') as handle:
            self.assertEqual(handle.read(), b'theirs')
        self.assertFalse(os.path.exists(dest + '.part'))

    def test_hash_mismatch_deletes_the_file(self):
        self.respond(FakeResponse())
        record = self.run_download(expected_sha256='00' * 32)
        self.assertEqual(record['status'], 'error')
        self.assertEqual(os.listdir(self.loras), [])

    def test_rejected_login_names_the_provider(self):
        self.respond(FakeResponse(status_code=401))
        record = self.run_download()
        self.assertEqual(record['status'], 'error')
        self.assertEqual(record['auth_required'], 'civitai')
        self.assertIn('Settings', record['error'])

    def test_token_goes_only_to_the_real_host(self):
        with mock.patch.object(settings, 'get_token', return_value='secret-token'):
            self.respond(FakeResponse())
            self.run_download(url='https://civitai.com.evil.example/x', filename='a.safetensors')
            self.run_download(url='https://civitai.com/api/download/models/1', filename='b.safetensors')
        self.assertNotIn('Authorization', self.sent[0][1])
        self.assertEqual(self.sent[1][1].get('Authorization'), 'Bearer secret-token')

    def test_not_enough_disk_space(self):
        self.respond(FakeResponse())
        usage = mock.Mock(free=10)
        with mock.patch.object(downloader.shutil, 'disk_usage', return_value=usage):
            record = self.run_download(expected_size=len(BODY))
        self.assertEqual(record['status'], 'error')
        self.assertIn('disk space', record['error'])

    def test_cancel_while_queued(self):
        download_id = downloader.start_download('https://civitai.com/x', 'loras', 'c.safetensors')
        self.assertTrue(downloader.cancel_download(download_id))
        downloader._run(download_id)
        self.assertEqual(downloader.get_progress(download_id)['status'], 'cancelled')
        self.assertEqual(self.sent, [])

    def test_completion_hook_receives_the_record(self):
        seen = threading.Event()
        received = {}

        def hook(record):
            received.update(record)
            seen.set()
            return 'written'

        downloader.add_completion_hook(hook)
        self.addCleanup(downloader._completion_hooks.remove, hook)
        self.respond(FakeResponse())
        record = self.run_download(filename='hooked.safetensors')
        self.assertTrue(seen.wait(5))
        self.assertEqual(received['sha256'], record['sha256'])
        self.assertEqual(received['category'], 'loras')
        self.assertTrue(received['path'].endswith('hooked.safetensors'))
