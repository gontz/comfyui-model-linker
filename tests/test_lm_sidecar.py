"""
Sidecars Lora Manager reads as its own. A sidecar missing a required key makes
Lora Manager skip the model entirely, so the required set is checked against
Lora Manager's published schema whenever it is installed alongside.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from core import civitai_meta, lm_sidecar

SHA = 'ab' * 32
LM_REQUIRED = ('file_name', 'model_name', 'file_path', 'size', 'modified', 'sha256', 'base_model', 'preview_url')
SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.pardir,
                      'comfyui-lora-manager', '.specs', 'metadata.schema.json')

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

    def test_autov3_three_states(self):
        self.assertEqual(lm_sidecar.build_sidecar(self.model, 'loras', SHA, VERSION)['autov3'], 'f7ad278a79a8')
        unmatched = dict(VERSION, files=[])
        self.assertIn('autov3', lm_sidecar.build_sidecar(self.model, 'loras', SHA, unmatched))
        self.assertIsNone(lm_sidecar.build_sidecar(self.model, 'loras', SHA, unmatched)['autov3'])
        self.assertNotIn('autov3', lm_sidecar.build_sidecar(self.model, 'loras', SHA, None))

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
