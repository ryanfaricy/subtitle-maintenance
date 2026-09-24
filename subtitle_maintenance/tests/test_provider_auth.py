"""Exercise helper authentication without importing Bazarr, credentials or HTTP deps."""

import ast
import io
import unittest
import urllib.error
from pathlib import Path

from subtitle_maintenance.providers import legacy_token_cooldown, provider_error_label


class ProviderAuthTests(unittest.TestCase):
    def helper(self):
        path = Path(__file__).resolve().parents[1] / "bazarr_bridge.py"
        if not path.exists():
            path = Path(__file__).resolve().parents[1] / "subtitle_maintenance/bazarr_bridge.py"
        tree = ast.parse(path.read_text())
        function = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "authenticated_download"
        )
        scope = {
            "urllib": __import__("urllib"),
            "TOKEN": "expired-test-token",
            "HOST": "api.opensubtitles.com",
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), scope)
        return scope

    def test_refresh_once_and_no_stale_bearer_on_login(self):
        scope = self.helper()
        headers = {}
        calls = []

        def call(host, path, payload):
            calls.append(path)
            if path == "login":
                self.assertNotIn("Authorization", headers)
                return {"token": "fresh-test-token"}
            if calls == ["download"]:
                raise urllib.error.HTTPError("test", 401, "invalid token", {}, io.BytesIO())
            self.assertEqual(headers["Authorization"], "Bearer fresh-test-token")
            return {"link": "test-result"}

        self.assertEqual(
            scope["authenticated_download"](
                call, headers, {"username": "test", "password": "test"}, 1
            ),
            {"link": "test-result"},
        )
        self.assertEqual(calls, ["download", "login", "download"])

    def test_repeated_401_stops(self):
        scope = self.helper()
        calls = []

        def call(host, path, payload):
            calls.append(path)
            if path == "login":
                return {"token": "test"}
            raise urllib.error.HTTPError("test", 401, "invalid", {}, io.BytesIO())

        with self.assertRaises(urllib.error.HTTPError) as caught:
            scope["authenticated_download"](call, {}, dict(username="test", password="test"), 1)
        caught.exception.close()
        self.assertEqual(calls, ["download", "login", "download"])
        self.assertIsNone(scope["TOKEN"])

    def test_quota_and_server_errors_not_retried(self):
        for status in [403, 406, 429, 500, 503]:
            scope = self.helper()
            calls = []

            def call(host, path, payload):
                calls.append(path)
                raise urllib.error.HTTPError("test", status, "error", {}, io.BytesIO())

            with self.assertRaises(urllib.error.HTTPError) as caught:
                scope["authenticated_download"](call, {}, {}, 1)
            caught.exception.close()
            self.assertEqual(calls, ["download"])

    def test_legacy_migration_is_narrow(self):
        saved = {"error": {"status": 401, "stage": "download", "message": "invalid token"}}
        self.assertTrue(legacy_token_cooldown(saved))
        self.assertFalse(legacy_token_cooldown(dict(saved, auth_recovery_version=1)))
        for status in [403, 406, 429, 500]:
            self.assertFalse(legacy_token_cooldown({"error": dict(saved["error"], status=status)}))
        self.assertIn("authentication", provider_error_label(saved["error"]))
