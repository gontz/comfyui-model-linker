"""Which engine takes a URL - decided by the real host."""

import hashlib
import json
import os
import types
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


def _line(event, **fields):
    return json.dumps({"event": event, **fields}) + "\n"


class _FakeStdin:
    def __init__(self):
        self.written = ''

    def write(self, s):
        self.written += s

    def flush(self):
        pass

    def close(self):
        pass


class _FakeStderr:
    def __init__(self, lines=None):
        self._lines = list(lines or [])

    def readlines(self):
        return list(self._lines)


class _FakeProc:
    """
    Stand-in for subprocess.Popen, real enough to catch the difference
    between "wait for it" and "kill it": `poll()` reports still-running
    until `wait()` or `kill()` is called, so a fake worker that simply
    finished producing its stdout (the normal case) is not mistaken for one
    that needs to be terminated.

    `stdout_lines` is any iterable of newline-delimited JSON lines - a list,
    or a generator for tests that need a side effect timed against a
    specific line being read. `stderr_lines` is a fixed list.
    """

    def __init__(self, stdout_lines, stderr_lines=None):
        self.stdin = _FakeStdin()
        self.stdout = iter(stdout_lines)
        self.stderr = _FakeStderr(stderr_lines)
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0
        self._exited = False

    def poll(self):
        return 0 if self._exited else None

    def wait(self, timeout=None):
        self.wait_calls += 1
        self._exited = True
        return 0

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1
        self._exited = True


class EngineGuaranteesTests(LibraryTestCase):
    """
    Pins each controller ruling from the Task 5 fix round against a fake
    worker process - no network, no real subprocess, nothing left running.
    """

    def setUp(self):
        super().setUp()
        try:
            from core import hf_downloader
        except ImportError:
            self.skipTest('huggingface_hub >= 1.1 is not installed')
        self.hf = hf_downloader
        self.loras = self.add_models('loras', [], ['.safetensors'])
        patcher = mock.patch.object(hf_downloader, 'get_token', return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _seed(self, dest_filename):
        """A progress record the way downloader.start_download would have
        left it, without going through the real queue/worker machinery."""
        download_id = downloader.generate_download_id()
        dest_path = os.path.join(self.loras, dest_filename)
        downloader.download_progress[download_id] = {
            'status': 'queued', 'progress': 0, 'total_size': 0,
            'downloaded': 0, 'speed': 0, 'filename': dest_filename, 'url': 'u',
            'error': None, 'start_time': None, 'category': 'loras', 'path': dest_path,
            'identity': downloader._identity(dest_path),
        }
        self.addCleanup(downloader.download_progress.pop, download_id, None)
        self.addCleanup(downloader.cancelled_downloads.discard, download_id)
        return download_id, dest_path

    def _run(self, download_id, proc, dest_filename, filename=None):
        with mock.patch.object(self.hf.subprocess, 'Popen', lambda *a, **k: proc):
            return self.hf.download_hf_model(
                'o/r', filename or dest_filename, self.loras, dest_filename, download_id)

    # -- item 1: free space >= size + headroom, even with no expected_size --

    def test_insufficient_disk_space_aborts_and_cleans_up(self):
        download_id, dest_path = self._seed('big.safetensors')
        proc = _FakeProc([_line('progress', n=1024, total=10 ** 15)])

        result = self._run(download_id, proc, 'big.safetensors')

        self.assertFalse(result)
        record = downloader.get_progress(download_id)
        self.assertEqual(record['status'], 'error')
        self.assertIn('disk space', record['error'].lower())
        self.assertFalse(os.path.exists(dest_path))
        self.assertFalse(os.path.exists(self.hf._staging_dir(self.loras, download_id)))
        self.assertGreaterEqual(proc.terminate_calls, 1)

    # -- ruling 4: the token never reaches the UI/console in error text ------

    def test_token_is_redacted_from_the_error_record(self):
        download_id, dest_path = self._seed('x.safetensors')
        self.hf.get_token.return_value = 'hf_SECRETTOKEN'
        proc = _FakeProc([_line('error', message='rejected: Bearer hf_SECRETTOKEN')])

        result = self._run(download_id, proc, 'x.safetensors')

        self.assertFalse(result)
        record = downloader.get_progress(download_id)
        self.assertEqual(record['status'], 'error')
        self.assertIn('***', record['error'])
        self.assertNotIn('hf_SECRETTOKEN', record['error'])

    # -- ruling 2: never clobber a destination that appeared meanwhile ------

    def test_done_with_existing_destination_does_not_overwrite(self):
        download_id, dest_path = self._seed('y.safetensors')
        with open(dest_path, 'w') as f:
            f.write('already here')
        staging = self.hf._staging_dir(self.loras, download_id)
        os.makedirs(staging, exist_ok=True)
        result_path = os.path.join(staging, 'y.safetensors')
        with open(result_path, 'w') as f:
            f.write('new payload')
        proc = _FakeProc([_line('done', path=result_path)])

        result = self._run(download_id, proc, 'y.safetensors')

        self.assertFalse(result)
        record = downloader.get_progress(download_id)
        self.assertEqual(record['status'], 'error')
        with open(dest_path) as f:
            self.assertEqual(f.read(), 'already here')
        self.assertFalse(os.path.exists(staging))

    # -- rulings 7/8 and item 1's terminate fix: the success path -----------

    def test_done_lands_the_file_and_leaves_a_non_terminal_status(self):
        download_id, dest_path = self._seed('z.safetensors')
        staging = self.hf._staging_dir(self.loras, download_id)
        os.makedirs(staging, exist_ok=True)
        result_path = os.path.join(staging, 'z.safetensors')
        with open(result_path, 'w') as f:
            f.write('payload')
        self.hf.get_token.return_value = 'hf_ABC'
        proc = _FakeProc([_line('done', path=result_path)])

        result = self._run(download_id, proc, 'z.safetensors')

        self.assertTrue(result)
        record = downloader.get_progress(download_id)
        # Ruling 7: never the terminal 'completed' status - no sha256 yet,
        # only downloader._run sets that, after hashing.
        self.assertEqual(record['status'], 'verifying')
        with open(dest_path) as f:
            self.assertEqual(f.read(), 'payload')
        self.assertFalse(os.path.exists(staging))
        # A worker that already finished producing output must not be
        # terminated - only waited for.
        self.assertEqual(proc.terminate_calls, 0)
        # Ruling 8: the endpoint is pinned rather than left to the
        # inherited environment's HF_ENDPOINT.
        sent = json.loads(proc.stdin.written)
        self.assertEqual(sent['endpoint'], 'https://huggingface.co')
        self.assertEqual(sent['token'], 'hf_ABC')

    # -- ruling 2 (cancellation half): a real kill, nothing left behind -----

    def test_cancel_mid_stream_leaves_nothing_at_dest(self):
        download_id, dest_path = self._seed('c.safetensors')

        def stdout_lines():
            # Set before the line is ever queued for the main loop to read,
            # so it is guaranteed visible by the time it matters - no race
            # to get right in the test.
            downloader.cancelled_downloads.add(download_id)
            yield _line('progress', n=1, total=100)

        proc = _FakeProc(stdout_lines())

        result = self._run(download_id, proc, 'c.safetensors')

        self.assertFalse(result)
        record = downloader.get_progress(download_id)
        self.assertEqual(record['status'], 'cancelled')
        self.assertFalse(os.path.exists(dest_path))
        self.assertFalse(os.path.exists(self.hf._staging_dir(self.loras, download_id)))
        self.assertGreaterEqual(proc.terminate_calls, 1)

    # -- item 9: an unsafe path inside the repo ------------------------------

    def test_unsafe_repo_path_is_refused_before_anything_is_spawned(self):
        for bad in ('../evil.safetensors', '/evil.safetensors', '\\evil.safetensors', 'C:evil.safetensors',
                    'sub/C:evil.safetensors'):
            with self.subTest(bad=bad):
                download_id, dest_path = self._seed('d.safetensors')

                # Not an exception: a Popen that *would* succeed, so the
                # assertion below actually discriminates "never spawned"
                # from "spawned, but its exception happened to also produce
                # an error record" - the old code already did the latter.
                spawn_calls = []

                def spy(*args, **kwargs):
                    spawn_calls.append(True)
                    return _FakeProc([])

                with mock.patch.object(self.hf.subprocess, 'Popen', spy):
                    result = self.hf.download_hf_model('o/r', bad, self.loras, 'd.safetensors', download_id)

                self.assertFalse(result)
                self.assertEqual(spawn_calls, [], 'a worker process must never be spawned for an unsafe repo path')
                record = downloader.get_progress(download_id)
                self.assertEqual(record['status'], 'error')

    def test_unsafe_repo_path_check_is_a_segment_check_not_a_substring_check(self):
        self.assertFalse(self.hf._unsafe_repo_path('a..b/x.safetensors'))
        self.assertFalse(self.hf._unsafe_repo_path('sub/model.safetensors'))


class RunHfTests(LibraryTestCase):
    """
    downloader._run_hf must decide success from the engine's return value,
    not from a status the engine is never allowed to set itself (ruling 7).
    A fake engine stands in here - this does not need huggingface_hub, since
    it is downloader.py's own dispatch logic under test, not hf_downloader.py.
    """

    def setUp(self):
        super().setUp()
        self.loras = self.add_models('loras', [], ['.safetensors'])
        patcher = mock.patch.object(downloader, '_ensure_workers')
        patcher.start()
        self.addCleanup(patcher.stop)

    def _start(self, filename):
        url = f'https://huggingface.co/o/r/resolve/main/{filename}'
        download_id = downloader.start_download(url, 'loras', filename)
        self.addCleanup(downloader.download_progress.pop, download_id, None)
        return download_id

    def test_true_leads_to_completed_with_a_real_hash(self):
        download_id = self._start('ok.safetensors')

        def fake_download_hf_model(repo, filename, dest_dir, dest_filename, did,
                                    revision='main', display_url=None):
            with open(os.path.join(dest_dir, dest_filename), 'wb') as f:
                f.write(b'payload')
            return True

        engine = (types.SimpleNamespace(download_hf_model=fake_download_hf_model),
                  {'repo': 'o/r', 'filename': 'ok.safetensors', 'branch': 'main'})
        with mock.patch.object(downloader, '_hf_engine_for', return_value=engine):
            downloader._run(download_id)

        record = downloader.get_progress(download_id)
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['sha256'], hashlib.sha256(b'payload').hexdigest())

    def test_false_leads_to_error_not_completed(self):
        download_id = self._start('bad.safetensors')

        def fake_download_hf_model(repo, filename, dest_dir, dest_filename, did,
                                    revision='main', display_url=None):
            downloader._set(did, error='synthetic failure')
            return False

        engine = (types.SimpleNamespace(download_hf_model=fake_download_hf_model),
                  {'repo': 'o/r', 'filename': 'bad.safetensors', 'branch': 'main'})
        with mock.patch.object(downloader, '_hf_engine_for', return_value=engine):
            downloader._run(download_id)

        record = downloader.get_progress(download_id)
        self.assertEqual(record['status'], 'error')
        self.assertEqual(record['error'], 'synthetic failure')
