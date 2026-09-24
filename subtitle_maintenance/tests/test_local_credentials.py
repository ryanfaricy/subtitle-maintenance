import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from local_credentials import get_secret


class LocalCredentialTests(unittest.TestCase):
    def test_environment_precedence(self):
        with patch.dict(os.environ, {'SONARR_API_KEY': 'test-only'}):
            self.assertEqual(get_secret('SONARR_API_KEY', '/missing'), 'test-only')

    def test_private_file_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            path = Path(d) / 'secrets.json'
            path.write_text(json.dumps({'SONARR_API_KEY': 'test-only'}))
            path.chmod(0o600)
            self.assertEqual(get_secret('SONARR_API_KEY', path), 'test-only')
            path.chmod(0o644)
            with self.assertRaises(RuntimeError): get_secret('SONARR_API_KEY', path)
            path.chmod(0o600)
            path.write_text('{invalid secret-value')
            with self.assertRaises(RuntimeError) as caught: get_secret('SONARR_API_KEY', path)
            self.assertNotIn('secret-value', str(caught.exception))
            path.unlink()
            with self.assertRaises(RuntimeError): get_secret('SONARR_API_KEY', path)

    def test_importer_legacy_config_without_executing_it(self):
        script = Path(__file__).resolve().parents[2] / 'plex-sonarr-import.py'
        if not script.exists(): self.skipTest('Installed importer integration test')
        spec = importlib.util.spec_from_file_location('importer_test', script)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            config = Path(d) / 'legacy.py'
            config.write_text('SONARR="http://localhost:8989/api/v3"\nAPI_KEY=get_secret("SONARR_API_KEY")\nraise RuntimeError("must not execute")\n')
            with patch.object(module, 'get_secret', return_value='test-only'):
                api = module.credentials(SimpleNamespace(legacy_config=config))
                self.assertEqual(api.key, 'test-only')
                self.assertEqual(api.url, 'http://localhost:8989/api/v3')
