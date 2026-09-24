import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path
from unittest.mock import patch

import script_config as settings

ROOT = Path(settings.__file__).resolve().parent


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class ConfigurationTests(unittest.TestCase):
    def test_discovery_precedence_and_relative_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            default = root / "subtitle-maintenance.json"
            default.write_text(json.dumps({"media_root": "./library"}))
            explicit = root / "override.json"
            explicit.write_text(json.dumps({"media_root": "./other", "model": "org/model"}))
            with patch.object(settings, "REPO_ROOT", root), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(settings.load_config()["media_root"], str(root / "library"))
                with patch.dict(os.environ, {settings.CONFIG_ENV: str(explicit)}):
                    self.assertEqual(settings.load_config()["model"], "org/model")
                    self.assertEqual(
                        settings.load_config(default)["media_root"], str(root / "library")
                    )
                default.unlink()
                self.assertEqual(settings.load_config(), {})
                with self.assertRaisesRegex(ValueError, "Cannot read config"):
                    settings.load_config(default)

    def test_virtualenv_python_keeps_its_environment(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            environment = root / "runtime"
            venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
            executable = environment / "bin/python"
            config = root / "config.json"
            for value in (str(executable), "./runtime/bin/python"):
                with self.subTest(value=value):
                    config.write_text(json.dumps({"python": value, "tools": {"ffmpeg": value}}))
                    loaded = settings.load_config(config)
                    self.assertEqual(loaded["python"], str(executable))
                    self.assertEqual(loaded["tools"]["ffmpeg"], str(executable))
                    result = subprocess.run(
                        [loaded["python"], "-c", "import sys; print(sys.prefix)"],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=30,
                    )
                    self.assertEqual(Path(result.stdout.strip()).resolve(), environment)

    def test_invalid_config_fails_without_echoing_contents(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            for content in ("private-value invalid", "[]", '{"tools": []}', '{"media_root": 3}'):
                p.write_text(content)
                with self.assertRaises(ValueError) as error:
                    settings.load_config(p)
                self.assertNotIn("private-value", str(error.exception))

    def test_tool_override_and_show_scope(self):
        config = {"tools": {"ffmpeg": sys.executable}, "media_root": "/example/media"}
        self.assertEqual(
            Path(settings.tool(config, "ffmpeg")).resolve(), Path(sys.executable).resolve()
        )
        self.assertEqual(settings.show_path(config, "QI"), Path("/example/media/TV/QI"))
        with self.assertRaises(ValueError):
            settings.show_path({}, "QI")
        with self.assertRaises(ValueError):
            settings.tool({"tools": {"ffmpeg": "/missing/tool"}}, "ffmpeg")

    def test_main_cli_config_root_and_state_without_media(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            (root / "empty").mkdir()
            config = root / "config.json"
            config.write_text(json.dumps({"media_root": "./empty", "state_dir": "./state"}))
            env = dict(os.environ, SUBTITLE_MAINTENANCE_CONFIG=str(config))
            result = subprocess.run(
                [sys.executable, str(ROOT / "subtitle-maintain.py"), "--scan-only"],
                cwd=d,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "state/state.db").exists())
            (root / "override-input").mkdir()
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "subtitle-maintain.py"),
                    str(root / "override-input"),
                    "--scan-only",
                    "--state-dir",
                    str(root / "override-state"),
                ],
                cwd=d,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "override-state/state.db").exists())
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "subtitle-maintain.py"),
                    "--scan-only",
                    "--config",
                    str(root / "missing.json"),
                ],
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Cannot read config", result.stderr)

    def test_whisper_cli_overrides_before_any_work(self):
        whisper = module("whisper-subtitles")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "media_root": str(root / "absent"),
                        "whisper": {"model": "configured", "state_dir": "./state"},
                        "tools": {name: sys.executable for name in ("ffmpeg", "ffprobe", "curl")},
                    }
                )
            )
            with (
                patch.object(
                    sys, "argv", ["whisper", "--config", str(config), "--model", "override"]
                ),
                patch.object(whisper, "check_asr_service") as service,
            ):
                self.assertEqual(whisper.main(), 0)
                service.assert_not_called()
                self.assertEqual(whisper.ASR_MODEL, "override")
                self.assertEqual(whisper.STATE_DIR, root / "state")
                self.assertFalse((root / "state").exists())

    def test_importer_reads_shared_url_without_loading_legacy_script(self):
        importer = module("plex-sonarr-import")
        from types import SimpleNamespace

        with (
            patch.object(
                importer,
                "load_config",
                return_value={"sonarr": {"url": "http://example.test/api/v3"}},
            ),
            patch.object(importer, "get_secret", return_value="fixture"),
            patch.dict(os.environ, {}, clear=True),
        ):
            api = importer.credentials(SimpleNamespace(config=None, legacy_config=None))
            self.assertEqual(api.url, "http://example.test/api/v3")

    def test_backup_command_keeps_paths_as_arguments(self):
        backup = module("subarr-daily-backup")
        config = {"backup": {"container": "test", "directory": "/a folder/backups", "keep": 9}}
        with (
            patch.object(sys, "argv", ["backup", "--apply"]),
            patch.object(backup, "load_config", return_value=config),
            patch.object(backup, "tool", return_value="/fake/docker"),
            patch.object(backup.subprocess, "run") as run,
        ):
            run.return_value = subprocess.CompletedProcess([], 0, "true\n", "")
            self.assertEqual(backup.main(), 0)
            command = run.call_args.args[0]
            self.assertEqual(command[-3:], ["/config/subarr.db", "/a folder/backups", "9"])
            self.assertIn("integrity_check", command[7])

    def test_common_command_uses_configured_tool(self):
        from subtitle_maintenance import common

        common.configure_tools({"tools": {"ffprobe": sys.executable}})
        try:
            self.assertEqual(
                common.run(["ffprobe", "-c", 'print("configured")']).strip(), "configured"
            )
        finally:
            common.configure_tools({})
