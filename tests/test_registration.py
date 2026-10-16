"""
Route registration in __init__.py: each optional part fails on its own, and
/analyze tells the frontend whether online lookups exist at all.

The extension package is executed under a throwaway name with every `core`
module aliased to the copy the tests already use, so nothing is imported
twice and no module state (or real data/) is touched.
"""

import asyncio
import importlib
import importlib.util
import json
import os
import sys
import types
import unittest
from unittest import mock

try:
    import requests  # noqa: F401
    from aiohttp import web
except ImportError:
    raise unittest.SkipTest('requests and aiohttp are needed')

EXTENSION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = 'model_linker_registration_under_test'
CORE_MODULES = ('linker', 'overrides', 'lookups', 'routes_downloads', 'routes_export', 'routes_settings')


def load_extension():
    """Run __init__.py against a fake PromptServer; returns its RouteTableDef."""
    for name in CORE_MODULES:
        importlib.import_module(f'core.{name}')
    routes = web.RouteTableDef()
    server = types.ModuleType('server')
    server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=routes))
    aliases = {f'{PACKAGE}.{name}': module for name, module in sys.modules.items()
               if name == 'core' or name.startswith('core.')}
    with mock.patch.dict(sys.modules, {'server': server, **aliases}):
        spec = importlib.util.spec_from_file_location(
            PACKAGE, os.path.join(EXTENSION_DIR, '__init__.py'), submodule_search_locations=[EXTENSION_DIR])
        module = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE] = module
        spec.loader.exec_module(module)
    return routes


def handler(routes, method, path):
    return next(r.handler for r in routes if r.method == method and r.path == path)


class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        # The download hook routes_downloads.register adds is process-wide
        from core import downloader
        hooks = list(downloader._completion_hooks)
        self.addCleanup(lambda: downloader._completion_hooks.__setitem__(slice(None), hooks))

    def paths(self, routes):
        return {r.path for r in routes}

    def test_a_failing_export_leaves_lookups_working(self):
        analysis = {'missing_models': [], 'total_missing': 0}
        with mock.patch('core.routes_export.register', side_effect=RuntimeError('export is broken')), \
             mock.patch('core.linker.analyze_and_find_matches', side_effect=lambda w: dict(analysis)), \
             mock.patch('core.lookups.attach_cached', return_value=3) as attach:
            routes = load_extension()
            response = asyncio.run(handler(routes, 'POST', '/model_linker/analyze')(
                FakeRequest({'workflow': {'nodes': []}})))
        paths = self.paths(routes)
        self.assertIn('/model_linker/lookups', paths)
        self.assertIn('/model_linker/download', paths)
        self.assertNotIn('/model_linker/export/lookup', paths)
        # /analyze still attaches lookups: Export failing did not switch them off
        self.assertTrue(attach.called)
        self.assertEqual(json.loads(response.text)['lookups_pending'], 3)

    def test_analyze_reports_whether_lookups_are_available(self):
        analysis = {'missing_models': [], 'total_missing': 0}
        with mock.patch('core.linker.analyze_and_find_matches', side_effect=lambda w: dict(analysis)), \
             mock.patch('core.lookups.attach_cached', return_value=0):
            routes = load_extension()
            response = asyncio.run(handler(routes, 'POST', '/model_linker/analyze')(
                FakeRequest({'workflow': {'nodes': []}})))
        self.assertTrue(json.loads(response.text)['lookups_available'])

    def test_analyze_says_so_when_lookups_are_unavailable(self):
        analysis = {'missing_models': [], 'total_missing': 0}
        with mock.patch('core.linker.analyze_and_find_matches', side_effect=lambda w: dict(analysis)), \
             mock.patch('core.routes_downloads.register', side_effect=ImportError('no requests')):
            routes = load_extension()
            response = asyncio.run(handler(routes, 'POST', '/model_linker/analyze')(
                FakeRequest({'workflow': {'nodes': []}})))
        self.assertNotIn('/model_linker/export/lookup', self.paths(routes))
        self.assertIs(json.loads(response.text)['lookups_available'], False)
