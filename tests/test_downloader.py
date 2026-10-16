"""
Downloads: where files may land, what is sent where, and that nothing
model-named is left behind by a failure.
"""

import errno
import hashlib
import os
import queue
import threading
import unittest
from unittest import mock

try:
    import requests
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import categories, downloader, overrides, settings
from support import LibraryTestCase

BODY = b'model weights' * 1000


class FakeResponse:
    def __init__(self, body=BODY, status_code=200, length=None, url=None, history=None):
        self.body = body
        self.status_code = status_code
        self.headers = {'content-length': str(len(body) if length is None else length)}
        # The final URL after any redirect. Left None lets the test harness
        # fill in the URL that was actually requested (the common case);
        # a test exercising a redirect away from https sets this explicitly.
        self.url = url
        # Each hop's own response, as `requests` would populate `.history`;
        # a test exercising an insecure intermediate hop passes a list of
        # simple objects exposing just the `.url` the check reads.
        self.history = history or []

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

        # No test here may reach the real network. A test that needs a
        # response installs its own fake via self.respond(); anything else
        # that still calls requests.get fails loudly - and is recorded
        # first, so a test asserting `self.sent == []` gets a true negative
        # rather than an empty list purely because nothing captured the call.
        def fail_by_default(url, headers=None, **kwargs):
            self.sent.append((url, dict(headers or {})))
            raise AssertionError(f'test reached the real network: {url}')
        patcher = mock.patch.object(requests, 'get', fail_by_default)
        patcher.start()
        self.addCleanup(patcher.stop)

        self._reset_module_state()

    def tearDown(self):
        self._reset_module_state()
        super().tearDown()

    def _reset_module_state(self):
        # download_progress/_jobs/_queue/cancelled_downloads are module-level
        # and shared by the whole process; a job left behind by one test
        # (or test module) must never be visible to, or processed by, another.
        downloader.download_progress.clear()
        downloader._jobs.clear()
        downloader.cancelled_downloads.clear()
        while True:
            try:
                downloader._queue.get_nowait()
            except queue.Empty:
                break

    def respond(self, response):
        def fake_get(url, headers=None, **kwargs):
            self.sent.append((url, dict(headers or {})))
            if response.url is None:
                response.url = url
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

    def test_malformed_url_is_rejected(self):
        # An unbalanced IPv6 literal makes urlparse (or its lazy .hostname
        # property) raise ValueError rather than just answer "not https".
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('https://[::1:bad/x', 'loras', 'a.safetensors')

    def test_folders_in_the_name_are_dropped(self):
        self.respond(FakeResponse())
        record = self.run_download(filename='..\\..\\escape.safetensors')
        self.assertEqual(record['status'], 'completed')
        self.assertTrue(os.path.isfile(os.path.join(self.loras, 'escape.safetensors')))

    def test_record_carries_canonical_category(self):
        # The frontend matches a running download to a card by canonical
        # category + filename, the same identity it dedupes downloads with -
        # the record must carry that canonical name, not just the raw one.
        download_id = downloader.start_download('https://civitai.com/x', 'loras', 'a.safetensors')
        record = downloader.get_progress(download_id)
        self.assertEqual(record['canonical_category'], categories.canonical_category('loras'))

    def test_rejects_drive_relative_and_hidden_names(self):
        # On Windows basename('C:evil.safetensors') is 'evil.safetensors', so
        # the colon must be refused in the name as given
        for name in ('C:evil.safetensors', 'a:b.safetensors', '.hidden.safetensors', '', 'x.exe'):
            with self.assertRaises(downloader.DownloadError, msg=name):
                downloader.start_download('https://civitai.com/x', 'loras', name)

    def test_rejects_forbidden_characters_in_filename(self):
        for name in ('a<b.safetensors', 'a>b.safetensors', 'a"b.safetensors', 'a|b.safetensors',
                     'a?b.safetensors', 'a*b.safetensors', 'a\x00b.safetensors', 'a\nb.safetensors'):
            with self.assertRaises(downloader.DownloadError, msg=name):
                downloader.start_download('https://civitai.com/x', 'loras', name)

    def test_rejects_unknown_category(self):
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('https://civitai.com/x', 'no_such_category', 'a.safetensors')

    def test_rejects_non_model_categories(self):
        # Registered with folder_paths, but not a model folder - the scanner
        # (core/scanner.py) skips cataloguing these too, from the same
        # NON_MODEL_CATEGORIES constant.
        for category, exts in (('custom_nodes', ['.py']), ('configs', ['.yaml']), ('datasets', [])):
            self.add_models(category, [], exts)
            with self.assertRaises(downloader.DownloadError, msg=category):
                downloader.start_download('https://civitai.com/x', category, 'a.safetensors')

    def test_never_overwrites(self):
        with open(os.path.join(self.loras, 'have.safetensors'), 'wb') as handle:
            handle.write(b'x')
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('https://civitai.com/x', 'loras', 'have.safetensors')

    def test_refuses_a_second_download_of_the_same_file(self):
        downloader.start_download('https://civitai.com/x', 'loras', 'twice.safetensors')
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('https://civitai.com/y', 'loras', 'twice.safetensors')

    def test_refuses_a_duplicate_that_only_differs_by_case(self):
        # Windows paths are case-insensitive; comparing the destination as
        # literal text (the old guard) missed this entirely.
        downloader.start_download('https://civitai.com/x', 'loras', 'Same.safetensors')
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('https://civitai.com/y', 'loras', 'same.safetensors')

    def test_refuses_a_duplicate_reached_through_a_linked_category(self):
        # Category folders are frequently junctions/symlinks to one shared
        # physical folder (checkpoints/unet/diffusion_models, etc). The
        # duplicate guard has to compare physical identity, not path text,
        # or two downloads to the same file through different category
        # names run at once and interleave writes into one .part file.
        self.link_category('loras_link', 'loras')
        downloader.start_download('https://civitai.com/x', 'loras', 'shared.safetensors')
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('https://civitai.com/y', 'loras_link', 'shared.safetensors')

    def test_rejects_boolean_size(self):
        # bool is an int subclass - a JSON `true` must not become a 1-byte
        # expected size.
        with self.assertRaises(downloader.DownloadError):
            downloader.start_download('https://civitai.com/x', 'loras', 'a.safetensors', expected_size=True)

    def test_rejects_malformed_expected_hash(self):
        for bad in ('not-a-hash', '00' * 31, '00' * 33, 'zz' * 32):
            with self.assertRaises(downloader.DownloadError, msg=bad):
                downloader.start_download('https://civitai.com/x', 'loras', 'a.safetensors', expected_sha256=bad)


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

    def test_file_appearing_while_queued_is_refused_without_a_request(self):
        download_id = downloader.start_download('https://civitai.com/x', 'loras', 'late.safetensors')
        with open(os.path.join(self.loras, 'late.safetensors'), 'wb') as handle:
            handle.write(b'theirs')
        downloader._run(download_id)
        record = downloader.get_progress(download_id)
        self.assertEqual(record['status'], 'error')
        self.assertEqual(self.sent, [])

    def test_hash_mismatch_deletes_the_file(self):
        self.respond(FakeResponse())
        record = self.run_download(expected_sha256='00' * 32)
        self.assertEqual(record['status'], 'error')
        self.assertEqual(os.listdir(self.loras), [])
        dest = os.path.join(self.loras, 'new.safetensors')
        self.assertFalse(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + '.part'))

    def test_hash_mismatch_never_names_the_file(self):          # item 3
        dest = os.path.join(self.loras, 'new.safetensors')
        named, real = [], {n: getattr(os, n) for n in ('replace', 'rename', 'link')}
        def spy(name):
            def f(src, dst, *a, **k):
                named.append(dst)
                return real[name](src, dst, *a, **k)
            return f
        self.respond(FakeResponse())
        with mock.patch.object(os, 'replace', spy('replace')), \
                mock.patch.object(os, 'rename', spy('rename')), \
                mock.patch.object(os, 'link', spy('link')):
            record = self.run_download(expected_sha256='00' * 32)
        self.assertEqual(record['status'], 'error')
        self.assertNotIn(dest, named)

    def test_rename_no_clobber_both_branches(self):              # item 6, unit
        for platform in ('win32', 'linux'):
            src = os.path.join(self.loras, 'src.part')
            dst = os.path.join(self.loras, 'dst.safetensors')
            for p, body in ((src, b'ours'), (dst, b'theirs')):
                with open(p, 'wb') as h:
                    h.write(body)
            with mock.patch.object(downloader.sys, 'platform', platform):
                with self.assertRaises(downloader.DownloadError, msg=platform):
                    downloader._rename_no_clobber(src, dst)
            with open(dst, 'rb') as h:
                self.assertEqual(h.read(), b'theirs', platform)
            self.assertTrue(os.path.exists(src), platform)
            os.remove(src); os.remove(dst)

    def test_refuses_a_redirect_to_non_https(self):
        self.respond(FakeResponse(url='http://evil.example/redirected'))
        record = self.run_download()
        self.assertEqual(record['status'], 'error')
        self.assertEqual(os.listdir(self.loras), [])

    def test_refuses_an_insecure_hop_even_if_the_final_url_is_https(self):
        # The final URL can be https while an earlier hop in the redirect
        # chain was not - still a point where the token (and now the whole
        # transfer) could have gone over an insecure connection.
        hop = mock.Mock(url='http://civitai.com/redirected-through-here')
        self.respond(FakeResponse(history=[hop]))
        record = self.run_download()
        self.assertEqual(record['status'], 'error')
        self.assertEqual(os.listdir(self.loras), [])

    def test_posix_without_hardlinks_still_completes(self):
        # Not every filesystem supports hard links (FAT/exFAT, some
        # SMB/NFS/FUSE mounts) - os.link raises some other OSError there,
        # not FileExistsError, even though the transfer itself is fine.
        self.respond(FakeResponse())
        with mock.patch.object(downloader.sys, 'platform', 'linux'), \
                mock.patch.object(os, 'link', side_effect=PermissionError(errno.EPERM, 'x')):
            record = self.run_download()
        self.assertEqual(record['status'], 'completed')
        with open(os.path.join(self.loras, 'new.safetensors'), 'rb') as handle:
            self.assertEqual(handle.read(), BODY)

    def test_posix_without_hardlinks_never_overwrites(self):
        # Same no-hardlink setup, but the destination appears mid-transfer -
        # the O_CREAT|O_EXCL fallback must refuse exactly like the normal
        # os.link path does, not silently replace it.
        dest = os.path.join(self.loras, 'new.safetensors')

        class Racing(FakeResponse):
            def iter_content(self, size):
                for i, chunk in enumerate(super().iter_content(size)):
                    if i == 1:
                        with open(dest, 'wb') as handle:
                            handle.write(b'theirs')
                    yield chunk

        self.respond(Racing())
        with mock.patch.object(downloader.sys, 'platform', 'linux'), \
                mock.patch.object(os, 'link', side_effect=PermissionError(errno.EPERM, 'x')):
            record = self.run_download()
        self.assertEqual(record['status'], 'error')
        with open(dest, 'rb') as handle:
            self.assertEqual(handle.read(), b'theirs')
        self.assertFalse(os.path.exists(dest + '.part'))

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

    def test_a_malformed_token_never_reaches_the_error_record(self):
        # A token with an embedded newline makes requests raise InvalidHeader,
        # whose message carries the whole Authorization value - which the
        # record's error is shown in the UI.
        token = 'hf_LEAKME0123456789\nX'

        def invalid_header(url, headers=None, **kwargs):
            raise requests.exceptions.InvalidHeader(
                f"Invalid leading whitespace in header value: {headers['Authorization']!r} ({token})")

        with mock.patch.object(settings, 'get_token', return_value=token), \
             mock.patch.object(requests, 'get', invalid_header):
            record = self.run_download()
        self.assertEqual(record['status'], 'error')
        self.assertNotIn('LEAKME', record['error'])
        self.assertIn('InvalidHeader', record['error'])

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

    def test_cancel_between_dequeue_and_start_is_kept(self):    # item 5
        dest = os.path.join(self.loras, 'new.safetensors')
        self.respond(FakeResponse())
        box, real = {}, downloader._check_disk
        def cancelling(directory, size):
            if 'done' not in box:
                box['done'] = True
                downloader.cancel_download(box['id'])
            return real(directory, size)
        box['id'] = downloader.start_download('https://civitai.com/api/download/models/1', 'loras', 'new.safetensors')
        with mock.patch.object(downloader, '_check_disk', cancelling):
            downloader._run(box['id'])
        self.assertEqual(downloader.get_progress(box['id'])['status'], 'cancelled')
        self.assertFalse(os.path.exists(dest))

    def test_cancel_during_transfer_removes_the_part(self):
        dest = os.path.join(self.loras, 'cancelme.safetensors')
        download_id_box = {}

        class Cancelling(FakeResponse):
            def iter_content(self, size):
                for i, chunk in enumerate(super().iter_content(size)):
                    if i == 1:
                        downloader.cancelled_downloads.add(download_id_box['id'])
                    yield chunk

        self.respond(Cancelling())
        download_id = downloader.start_download('https://civitai.com/x', 'loras', 'cancelme.safetensors')
        download_id_box['id'] = download_id
        downloader._run(download_id)
        record = downloader.get_progress(download_id)
        self.assertEqual(record['status'], 'cancelled')
        self.assertFalse(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + '.part'))
        self.assertNotIn(download_id, downloader.cancelled_downloads)

    def test_file_appearing_after_the_last_check_is_kept(self):  # item 6, integration
        dest = os.path.join(self.loras, 'new.safetensors')
        real_exists = os.path.exists
        self.respond(FakeResponse())
        download_id = downloader.start_download('https://civitai.com/api/download/models/1', 'loras', 'new.safetensors')
        with open(dest, 'wb') as h:
            h.write(b'theirs')
        with mock.patch.object(os.path, 'exists', lambda p: False if p == dest else real_exists(p)):
            downloader._run(download_id)
        self.assertEqual(downloader.get_progress(download_id)['status'], 'error')
        with open(dest, 'rb') as h:
            self.assertEqual(h.read(), b'theirs')

    def test_missing_category_folder_with_unknown_size(self):    # item 9
        os.rmdir(self.loras)
        self.respond(FakeResponse(length=0))
        self.assertEqual(self.run_download()['status'], 'completed')

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
