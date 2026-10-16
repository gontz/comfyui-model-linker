"""
Sidecars Lora Manager reads as its own. A sidecar missing a required key makes
Lora Manager skip the model entirely, so the required set is checked against
Lora Manager's published schema whenever it is installed alongside.
"""

import importlib.util
import json
import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

try:
    import requests
except ImportError:
    raise unittest.SkipTest('requests is not installed')

from core import civitai_meta, downloader, lm_sidecar

SHA = 'ab' * 32
LM_REQUIRED = ('file_name', 'model_name', 'file_path', 'size', 'modified', 'sha256', 'base_model', 'preview_url')
# Lora Manager's install directory is found the same way lm_sidecar itself
# finds it (case-insensitively, under custom_nodes) - a hardcoded relative
# path here would silently skip the schema test the moment that lookup
# changed, which is exactly what must never happen.
SCHEMA = os.path.join(lm_sidecar._LORA_MANAGER_DIR, '.specs', 'metadata.schema.json')

VERSION = {
    'id': 3090220, 'modelId': 2224836, 'name': 'v1', 'baseModel': 'SDXL Lightning', 'trainedWords': ['zz'],
    'model': {'name': 'Rebel', 'type': 'LORA', 'description': '<p>d</p>', 'tags': ['style']},
    'files': [
        {'name': 'other.safetensors', 'hashes': {'SHA256': 'CD' * 32, 'AutoV3': '111111111111AA'}},
        {'name': 'rebel.safetensors', 'hashes': {'SHA256': SHA.upper(), 'AutoV3': 'F7AD278A79A8FF'}},
    ],
    'images': [
        {'url': 'https://image.civitai.com/x/abc/original=true/1.jpeg', 'nsfwLevel': 8, 'type': 'image'},
        {'url': 'https://image.civitai.com/x/def/original=true/2.jpeg', 'nsfwLevel': 1, 'type': 'image',
         'meta': {'prompt': 'p', 'comfy': '{huge}'}},
    ],
}


class SidecarTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='model-linker-lm-')
        self.model = os.path.join(self.root, 'rebel.safetensors')
        with open(self.model, 'wb') as handle:
            handle.write(b'weights')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class FormatTests(SidecarTestCase):
    def test_required_keys_and_types(self):
        data = lm_sidecar.build_sidecar(self.model, 'loras', SHA.upper(), VERSION, now=123.0)
        for key in LM_REQUIRED:
            self.assertIn(key, data)
        self.assertEqual(data['file_name'], 'rebel')
        self.assertEqual(data['model_name'], 'Rebel')
        self.assertEqual(data['sha256'], SHA)
        self.assertIsInstance(data['size'], int)
        self.assertIsInstance(data['modified'], float)
        self.assertNotIn('\\', data['file_path'])
        self.assertTrue(os.path.isabs(data['file_path'].replace('/', os.sep)))
        self.assertEqual(data['metadata_source'], 'civitai_api')
        self.assertTrue(data['from_civitai'])
        self.assertEqual(data['hash_status'], 'completed')
        self.assertEqual(data['tags'], ['style'])
        self.assertEqual(data['modelDescription'], '<p>d</p>')

    def test_type_specific_fields(self):
        lora = lm_sidecar.build_sidecar(self.model, 'loras', SHA, VERSION)
        self.assertEqual(lora['usage_tips'], '{}')
        self.assertNotIn('sub_type', lora)
        for category, sub_type in (('checkpoints', 'checkpoint'), ('diffusion_models', 'diffusion_model'),
                                   ('unet', 'diffusion_model'), ('embeddings', 'embedding'),
                                   ('vae', 'vae'), ('clip', 'text_encoder')):
            data = lm_sidecar.build_sidecar(self.model, category, SHA, VERSION)
            self.assertEqual(data['sub_type'], sub_type, category)
            self.assertNotIn('usage_tips', data, category)
            self.assertNotIn('model_type', data, category)

    def test_autov3_present_only_when_civitai_actually_has_one(self):
        # Deliberate deviation from the published contract (ruling, fix round
        # 1): a JSON `null` tells Lora Manager "checked, CivitAI has none"
        # and stops it computing the value itself from the file's own
        # header. So the key is written only on an actual match; every other
        # case - CivitAI checked and had nothing, or never checked at all -
        # omits the key rather than nulling it.
        self.assertEqual(lm_sidecar.build_sidecar(self.model, 'loras', SHA, VERSION)['autov3'], 'f7ad278a79a8')
        unmatched = dict(VERSION, files=[])
        self.assertNotIn('autov3', lm_sidecar.build_sidecar(self.model, 'loras', SHA, unmatched))
        self.assertNotIn('autov3', lm_sidecar.build_sidecar(self.model, 'loras', SHA, None))

    def test_tolerates_malformed_civitai_shapes(self):
        """
        A non-dict files[] entry, a non-dict hashes, or a non-dict
        version['model'] must be skipped or ignored, never raised - CivitAI
        is a third party, and a shape it hands back in some edge case is not
        a reason to lose the whole sidecar.
        """
        odd_version = dict(
            VERSION,
            model='not an object',
            files=[
                'not an object either',
                {'name': 'x.safetensors', 'hashes': 'also not an object'},
                {'name': 'rebel.safetensors', 'hashes': {'SHA256': SHA.upper(), 'AutoV3': 'AABBCCDDEEFF'}},
            ],
        )
        data = lm_sidecar.build_sidecar(self.model, 'loras', SHA, odd_version)
        self.assertEqual(data['model_name'], 'rebel')  # falls back to the file's own stem
        self.assertEqual(data['tags'], [])
        self.assertEqual(data['modelDescription'], '')
        # The well-shaped third entry is still reachable past the two junk ones.
        self.assertEqual(data['autov3'], 'aabbccddeeff')

    def test_unknown_to_civitai(self):
        data = lm_sidecar.build_sidecar(self.model, 'loras', SHA, None)
        self.assertFalse(data['from_civitai'])
        self.assertEqual(data['civitai'], {})
        self.assertIsNone(data['metadata_source'])
        self.assertEqual(data['base_model'], 'Unknown')
        self.assertEqual(data['model_name'], 'rebel')

    def test_base_model_mapping_matches_lora_manager(self):
        cases = {None: 'Unknown', '': 'Unknown', 'SDXL 1.0': 'SDXL 1.0', 'SDXL Lightning': 'SDXL 1.0',
                 'Pony V7': 'Pony', 'Illustrious': 'Illustrious', 'Flux.1 D': 'Flux.1 D',
                 'Flux.1 Krea': 'Flux.1 Krea', 'SD 1.5': 'SD 1.5', 'ZImageTurbo': 'ZImageTurbo',
                 # Lora Manager's "Hunyuan Video" key is never matched (it lowercases
                 # the input only), so the string is kept as CivitAI gave it
                 'Hunyuan Video 1.5': 'Hunyuan Video 1.5'}
        for given, expected in cases.items():
            self.assertEqual(lm_sidecar.determine_base_model(given), expected, given)

    def test_unmanaged_categories_get_nothing(self):
        self.assertIsNone(lm_sidecar.lm_target('style_models'))
        self.assertEqual(lm_sidecar.write_sidecar(self.model, 'style_models', SHA), 'skipped')

    @unittest.skipUnless(os.path.isfile(SCHEMA), 'Lora Manager is not installed alongside')
    def test_satisfies_lora_managers_published_schema(self):
        with open(SCHEMA, encoding='utf-8') as handle:
            schema = json.load(handle)
        branches = {b['title']: b for b in schema['oneOf']}
        types = {'string': str, 'integer': int, 'number': (int, float), 'boolean': bool,
                 'array': list, 'object': dict, 'null': type(None)}
        for title, category in (('LoRA Model Metadata', 'loras'), ('Checkpoint Model Metadata', 'checkpoints'),
                                ('Embedding Model Metadata', 'embeddings')):
            data = lm_sidecar.build_sidecar(self.model, category, SHA, VERSION)
            branch = branches[title]
            for key in branch.get('required', []):
                self.assertIn(key, data, f'{title}: {key}')
            for key, spec in branch.get('properties', {}).items():
                if key not in data or 'type' not in spec:
                    continue
                allowed = spec['type'] if isinstance(spec['type'], list) else [spec['type']]
                ok = any(isinstance(data[key], types[t]) and not (t in ('integer', 'number') and isinstance(data[key], bool))
                         for t in allowed)
                self.assertTrue(ok, f'{title}: {key}={data[key]!r} is not {allowed}')


class WriteTests(SidecarTestCase):
    def setUp(self):
        super().setUp()
        self.notified = []
        patches = [
            mock.patch.object(lm_sidecar, 'notify_lora_manager',
                              lambda prefix, path, fetch=False: self.notified.append((prefix, fetch))),
            mock.patch.object(lm_sidecar, 'centralized_sidecars', return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def read(self):
        with open(os.path.join(self.root, 'rebel.metadata.json'), encoding='utf-8') as handle:
            return json.load(handle)

    def test_writes_sidecar_and_preview(self):
        def fake_preview(model_path, image):
            path = os.path.splitext(model_path)[0] + '.jpeg'
            with open(path, 'wb') as handle:
                handle.write(b'jpg')
            return path

        with mock.patch.object(civitai_meta, 'version_for_sha256', return_value=VERSION), \
             mock.patch.object(lm_sidecar, 'download_preview', side_effect=fake_preview) as preview:
            self.assertEqual(lm_sidecar.write_sidecar(self.model, 'loras', SHA), 'written')
        data = self.read()
        self.assertTrue(data['preview_url'].endswith('/rebel.jpeg'))
        self.assertEqual(data['preview_nsfw_level'], 1)  # the safe image was chosen
        self.assertEqual(preview.call_args[0][1]['nsfwLevel'], 1)
        self.assertEqual(self.notified, [('loras', False)])

    def test_civitai_down_still_writes_a_loadable_sidecar(self):
        with mock.patch.object(civitai_meta, 'version_for_sha256',
                               side_effect=civitai_meta.CivitaiUnavailable('timeout')):
            self.assertEqual(lm_sidecar.write_sidecar(self.model, 'loras', SHA), 'written')
        data = self.read()
        for key in LM_REQUIRED:
            self.assertIn(key, data)
        self.assertFalse(data['from_civitai'])
        self.assertEqual(self.notified, [('loras', True)])  # Lora Manager is asked to fetch it itself

    def test_never_overwrites_an_existing_sidecar(self):
        path = os.path.join(self.root, 'rebel.metadata.json')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('{"favorite": true}')
        self.assertEqual(lm_sidecar.write_sidecar(self.model, 'loras', SHA), 'exists')
        with open(path, encoding='utf-8') as handle:
            self.assertEqual(handle.read(), '{"favorite": true}')

    def test_centralized_mode_is_left_alone(self):
        with mock.patch.object(lm_sidecar, 'centralized_sidecars', return_value=True):
            self.assertEqual(lm_sidecar.write_sidecar(self.model, 'loras', SHA), 'skipped')

    def test_no_civitai_fetch_when_told_not_to(self):
        with mock.patch.object(civitai_meta, 'version_for_sha256', return_value=None):
            lm_sidecar.write_sidecar(self.model, 'loras', SHA, fetch_if_unknown=False)
        self.assertEqual(self.notified, [('loras', False)])

    def test_sidecar_race_with_lora_managers_own_write(self):
        """
        write_sidecar checks the sidecar is missing, then spends time on
        CivitAI and the preview before landing its own file. If Lora
        Manager's own scan (triggered by some other download) writes that
        exact sidecar in the gap, this call must not clobber it.
        """
        path = os.path.join(self.root, 'rebel.metadata.json')

        def sneaky(sha256):
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('{"written_by": "someone else"}')
            return None  # no further lookups needed to prove the point

        with mock.patch.object(civitai_meta, 'version_for_sha256', side_effect=sneaky):
            self.assertEqual(lm_sidecar.write_sidecar(self.model, 'loras', SHA), 'exists')
        with open(path, encoding='utf-8') as handle:
            self.assertEqual(handle.read(), '{"written_by": "someone else"}')
        # No leftover temp file from the losing write.
        self.assertEqual([f for f in os.listdir(self.root) if f.endswith('.tmp')], [])
        self.assertEqual(self.notified, [])  # a discarded write never notifies Lora Manager

    def test_malformed_preview_fields_do_not_block_the_sidecar(self):
        """A bad nsfwLevel or an unparseable image URL from CivitAI means
        'no preview', never a crash that leaves no sidecar at all."""
        version = dict(VERSION, images=[
            {'url': 'https://[invalid/original=true/1.jpeg', 'nsfwLevel': 'spicy', 'type': 'image'},
        ])
        with mock.patch.object(civitai_meta, 'version_for_sha256', return_value=version):
            self.assertEqual(lm_sidecar.write_sidecar(self.model, 'loras', SHA), 'written')
        data = self.read()
        self.assertEqual(data['preview_url'], '')
        self.assertEqual(data['preview_nsfw_level'], 0)

    def test_a_real_landing_failure_propagates_instead_of_reporting_exists(self):
        """
        _land_file must only treat an actual collision (DestinationExists)
        as the benign 'exists' outcome. A real failure landing the sidecar -
        a permissions problem, an antivirus lock, anything else
        _rename_no_clobber can raise - must propagate, so the download
        pipeline's own hook-exception handling records it as the failure it
        is instead of a falsely reassuring 'exists'.
        """
        with mock.patch.object(civitai_meta, 'version_for_sha256', return_value=None), \
             mock.patch.object(downloader, '_rename_no_clobber',
                               side_effect=downloader.DownloadError('Could not save rebel.metadata.json: '
                                                                     '[WinError 32] file is in use')):
            with self.assertRaises(downloader.DownloadError):
                lm_sidecar.write_sidecar(self.model, 'loras', SHA)
        # Nothing was left behind by the failed attempt.
        self.assertFalse(os.path.exists(os.path.join(self.root, 'rebel.metadata.json')))
        self.assertEqual([f for f in os.listdir(self.root) if f.endswith('.tmp')], [])

    def test_records_a_preview_that_appeared_after_losing_the_landing_race(self):
        """
        download_preview losing its own landing race doesn't mean there is
        no preview now - it means someone else's (another download's, or
        Lora Manager's own) is already sitting there. The sidecar must
        record that file, not an empty preview_url.
        """
        def fake_download_preview(model_path, image):
            path = os.path.splitext(model_path)[0] + '.jpeg'
            with open(path, 'wb') as handle:
                handle.write(b'already landed by someone else')
            return ''  # as if _land_file lost the race

        with mock.patch.object(civitai_meta, 'version_for_sha256', return_value=VERSION), \
             mock.patch.object(lm_sidecar, 'download_preview', side_effect=fake_download_preview):
            self.assertEqual(lm_sidecar.write_sidecar(self.model, 'loras', SHA), 'written')
        data = self.read()
        self.assertTrue(data['preview_url'].endswith('/rebel.jpeg'))

    @unittest.skipUnless(importlib.util.find_spec('requests'), 'requests is not installed')
    def test_huggingface_download_is_hydrated_by_lora_manager(self):
        calls = []
        with mock.patch.object(civitai_meta, 'version_for_sha256', return_value=None), \
             mock.patch('core.lm_api.link_huggingface', lambda *a: calls.append('link')), \
             mock.patch('core.lm_api.hydrate_huggingface', lambda *a: calls.append('hydrate')), \
             mock.patch('core.downloader.category_directory', return_value=self.root):
            outcome = lm_sidecar.on_download_complete({
                'path': self.model, 'category': 'loras', 'sha256': SHA,
                'url': 'https://huggingface.co/o/r/resolve/main/rebel.safetensors'})
        self.assertEqual(outcome, 'written+huggingface')
        self.assertEqual(calls, ['link', 'hydrate'])
        self.assertEqual(self.notified, [('loras', False)])  # no CivitAI fetch for a HF file


class LoraManagerSettingsTests(unittest.TestCase):
    """settings.json is found where Lora Manager itself looks for it."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='model-linker-lm-settings-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.lm_dir = os.path.join(self.root, 'comfyui-lora-manager')
        self.user_dir = os.path.join(self.root, 'user')
        os.makedirs(self.lm_dir)
        os.makedirs(self.user_dir)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in ('LORA_MANAGER_SETTINGS_DIR', 'LORA_MANAGER_PORTABLE'):
            os.environ.pop(name, None)
        for patcher in (mock.patch.object(lm_sidecar, '_LORA_MANAGER_DIR', self.lm_dir),
                        mock.patch.object(lm_sidecar, '_user_config_dir', return_value=self.user_dir)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, folder, data):
        with open(os.path.join(folder, 'settings.json'), 'w', encoding='utf-8') as handle:
            json.dump(data, handle)

    def test_per_user_folder_unless_portable(self):
        self.write(self.lm_dir, {'sidecar_storage_mode': 'centralized'})  # a stale copy
        self.write(self.user_dir, {'sidecar_storage_mode': 'alongside'})
        self.assertEqual(lm_sidecar.lora_manager_settings_path(), os.path.join(self.user_dir, 'settings.json'))
        self.assertFalse(lm_sidecar.centralized_sidecars())

    def test_portable_mode_uses_its_own_folder(self):
        self.write(self.lm_dir, {'use_portable_settings': True, 'sidecar_storage_mode': 'centralized'})
        self.assertTrue(lm_sidecar.centralized_sidecars())
        os.environ['LORA_MANAGER_PORTABLE'] = '0'  # explicit opt-out wins over the file
        self.assertEqual(lm_sidecar.lora_manager_settings_path(), os.path.join(self.user_dir, 'settings.json'))

    def test_settings_dir_override_wins(self):
        self.write(self.lm_dir, {'use_portable_settings': True})
        os.environ['LORA_MANAGER_SETTINGS_DIR'] = self.root
        self.assertEqual(lm_sidecar.lora_manager_settings_path(), os.path.join(self.root, 'settings.json'))


class CivitaiMetaTests(unittest.TestCase):
    def test_enrichment_and_comfy_metadata_removal(self):
        model = {'description': 'full', 'tags': ['a'], 'creator': {'username': 'u'},
                 'allowNoCredit': True, 'allowCommercialUse': ['Image']}
        version = civitai_meta.strip_comfy_metadata(civitai_meta.enrich_version(VERSION, model))
        self.assertEqual(version['model']['description'], 'full')
        self.assertEqual(version['model']['tags'], ['a'])
        self.assertEqual(version['model']['allowCommercialUse'], ['Image'])
        self.assertEqual(version['creator'], {'username': 'u'})
        self.assertNotIn('comfy', version['images'][1]['meta'])
        self.assertIn('comfy', VERSION['images'][1]['meta'])  # the input is not mutated


class FakeJsonResponse:
    """Stand-in for `requests.Response` where only `.status_code`/`.json()` matter."""

    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}

    def json(self):
        return self._data


class CivitaiMetaNetworkTests(unittest.TestCase):
    """No real network: `requests.get` is replaced for every case here."""

    def test_404_means_civitai_does_not_know_the_file(self):
        with mock.patch('requests.get', return_value=FakeJsonResponse(404)):
            self.assertIsNone(civitai_meta.version_for_sha256(SHA))

    def test_never_queries_for_the_empty_file_hash(self):
        """A file that failed to read can hash to the SHA-256 of zero bytes.
        That is never a real file CivitAI could usefully answer for, so no
        request is made at all - checked case-insensitively, since the
        downloader always lowercases hashes but nothing enforces that here."""
        with mock.patch('requests.get') as get:
            self.assertIsNone(civitai_meta.version_for_sha256(civitai_meta.EMPTY_FILE_SHA256.upper()))
        get.assert_not_called()

    def test_other_non_200_raises_unavailable(self):
        with mock.patch('requests.get', return_value=FakeJsonResponse(500)):
            with self.assertRaises(civitai_meta.CivitaiUnavailable):
                civitai_meta.version_for_sha256(SHA)

    def test_token_never_reaches_the_unavailable_message(self):
        """
        Regression for 40bb25a: some requests exceptions (InvalidHeader on a
        malformed token) embed the full Authorization header, token
        included, in str(e). civitai_meta must never let that reach a log
        line via CivitaiUnavailable's message.
        """
        with mock.patch('requests.get',
                        side_effect=requests.exceptions.InvalidHeader(
                            "Invalid return character or leading space in header: "
                            "Authorization: Bearer SECRET-TOKEN-VALUE")):
            with self.assertRaises(civitai_meta.CivitaiUnavailable) as ctx:
                civitai_meta.version_for_sha256(SHA)
        self.assertNotIn('SECRET-TOKEN-VALUE', str(ctx.exception))


class PreviewDownloadTests(SidecarTestCase):
    """`download_preview`'s own guards - host, scheme, extension, and every redirect hop."""

    URL = 'https://civitai.com/x/abc/original=true/1.jpeg'

    class _FakeStream:
        def __init__(self, body=b'jpg', status_code=200, url=None, history=()):
            self.body = body
            self.status_code = status_code
            self.url = url
            self.history = history

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def iter_content(self, chunk_size):
            yield self.body

    def test_refuses_a_non_civitai_host(self):
        self.assertEqual(lm_sidecar.download_preview(self.model, {'url': 'https://evil.example/1.jpeg'}), '')

    def test_refuses_plain_http(self):
        self.assertEqual(lm_sidecar.download_preview(self.model, {'url': 'http://civitai.com/1.jpeg'}), '')

    def test_refuses_an_unsaveable_extension(self):
        self.assertEqual(
            lm_sidecar.download_preview(self.model, {'url': 'https://civitai.com/original=true/1.mp4'}), '')

    def test_refuses_a_malformed_url_without_raising(self):
        self.assertEqual(lm_sidecar.download_preview(self.model, {'url': 'https://[invalid/1.jpeg'}), '')

    def test_a_failure_creating_the_temp_file_returns_empty_string(self):
        """mkstemp itself (a permissions problem, a full disk) is still just
        a failure to download a preview - the documented 'returns "" on any
        failure' contract, not an exception out of this function."""
        with mock.patch.object(lm_sidecar.tempfile, 'mkstemp', side_effect=OSError('disk full')):
            self.assertEqual(lm_sidecar.download_preview(self.model, {'url': self.URL}), '')

    def test_refuses_a_redirect_to_another_host(self):
        with mock.patch('requests.get',
                        return_value=self._FakeStream(url='https://evil.example/1.jpeg')):
            self.assertEqual(lm_sidecar.download_preview(self.model, {'url': self.URL}), '')
        self.assertFalse(os.path.isfile(os.path.splitext(self.model)[0] + '.jpeg'))

    def test_refuses_a_redirect_hop_that_dropped_to_http(self):
        hop = types.SimpleNamespace(url='http://civitai.com/x/abc/original=true/1.jpeg')
        with mock.patch('requests.get',
                        return_value=self._FakeStream(url='https://civitai.com/x/w=450/1.jpeg', history=[hop])):
            self.assertEqual(lm_sidecar.download_preview(self.model, {'url': self.URL}), '')

    def test_downloads_a_valid_preview_and_cleans_up_its_temp_file(self):
        with mock.patch('requests.get', return_value=self._FakeStream(url='https://civitai.com/x/w=450/1.jpeg')):
            path = lm_sidecar.download_preview(self.model, {'url': self.URL})
        self.assertEqual(path, os.path.splitext(self.model)[0] + '.jpeg')
        self.assertTrue(os.path.isfile(path))
        self.assertEqual([f for f in os.listdir(self.root) if f.endswith('.tmp')], [])

    def test_never_overwrites_a_preview_that_appeared_mid_download(self):
        dest = os.path.splitext(self.model)[0] + '.jpeg'

        class RaceStream(self._FakeStream):
            def __enter__(self):
                with open(dest, 'wb') as handle:
                    handle.write(b'already here')
                return self

        with mock.patch('requests.get', return_value=RaceStream(url='https://civitai.com/x/w=450/1.jpeg')):
            self.assertEqual(lm_sidecar.download_preview(self.model, {'url': self.URL}), '')
        with open(dest, 'rb') as handle:
            self.assertEqual(handle.read(), b'already here')
        self.assertEqual([f for f in os.listdir(self.root) if f.endswith('.tmp')], [])


class ApplyUmaskTests(unittest.TestCase):
    """
    `mkstemp` always creates 0600, ignoring the umask. `_apply_umask` must
    bring a landed file back to a normal write's permissions on POSIX, and
    do nothing on Windows (which has no such bits). Exercised by mocking
    `os.name`/`os.chmod` rather than real filesystem permissions, since this
    machine is Windows - cheap, and it is `_apply_umask`'s own arithmetic
    being checked, not the OS's.
    """

    def test_computes_mode_from_the_process_umask_on_posix(self):
        with mock.patch.object(lm_sidecar.os, 'name', 'posix'), \
             mock.patch.object(lm_sidecar, '_UMASK', 0o022), \
             mock.patch.object(lm_sidecar.os, 'chmod') as chmod:
            lm_sidecar._apply_umask('/some/tmp/file')
        chmod.assert_called_once_with('/some/tmp/file', 0o644)

    def test_is_a_no_op_on_windows(self):
        with mock.patch.object(lm_sidecar.os, 'name', 'nt'), \
             mock.patch.object(lm_sidecar.os, 'chmod') as chmod:
            lm_sidecar._apply_umask('/some/tmp/file')
        chmod.assert_not_called()


class NotifyLoraManagerTests(unittest.TestCase):
    """
    `notify_lora_manager` is best effort and now delegates the actual HTTP to
    `lm_api` (Task 9). The scoped-scan-falls-back-to-a-full-scan behaviour
    this used to assert directly now lives in, and is covered by,
    `lm_api.scan` itself (see `tests/test_lm_backfill.py`'s
    `LmApiTests.test_scan_falls_back_to_a_full_scan`); what is left to check
    here is that `notify_lora_manager` calls `lm_api` with the right
    arguments and swallows `LoraManagerUnavailable`.
    """

    def setUp(self):
        self.model = os.path.join(tempfile.mkdtemp(prefix='model-linker-lm-notify-'), 'rebel.safetensors')
        self.addCleanup(shutil.rmtree, os.path.dirname(self.model), True)

    def test_scans_only_the_models_own_root(self):
        from core import lm_api
        with mock.patch.object(lm_api, 'scan') as scan, mock.patch.object(lm_api, '_call') as call:
            lm_sidecar.notify_lora_manager('loras', self.model, fetch=False)
        scan.assert_called_once_with('loras', root=os.path.dirname(self.model))
        call.assert_not_called()

    def test_fetch_asks_lora_manager_to_pull_civitai_metadata_itself(self):
        from core import lm_api
        with mock.patch.object(lm_api, 'scan'), mock.patch.object(lm_api, '_call') as call:
            lm_sidecar.notify_lora_manager('loras', self.model, fetch=True)
        call.assert_called_once_with('POST', '/loras/fetch-civitai',
                                     json={'file_path': lm_api.lm_path(self.model)})

    def test_lora_manager_unavailable_is_swallowed(self):
        from core import lm_api
        with mock.patch.object(lm_api, 'scan', side_effect=lm_api.LoraManagerUnavailable('down')):
            lm_sidecar.notify_lora_manager('loras', self.model, fetch=True)  # must not raise
