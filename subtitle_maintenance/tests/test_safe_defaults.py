"""Regression tests for omission, preview, and import safety; never touch real media."""

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import script_config
from subtitle_maintenance import cli, doctor
from subtitle_maintenance.config_schema import validate

ROOT = Path(script_config.__file__).resolve().parent


def load_script(path):
    spec = importlib.util.spec_from_file_location("safety_" + path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {spec.name: module}):
        spec.loader.exec_module(module)
    return module


class SafeDefaultsTests(unittest.TestCase):
    def test_all_scripts_import_without_actions(self):
        # Only public checkout scripts; development copies may retain private migrations.
        names = (
            "subtitle-maintain.py",
            "eastenders-fix.py",
            "whisper-subtitles.py",
            "subarr-daily-backup.py",
            "plex-sonarr-import.py",
            "script_config.py",
            "local_credentials.py",
            "audit_default_english_subtitles.py",
            "audit_redundant_forced_subtitles.py",
            "fix_qi_english_subtitle_language.py",
            "remove_redundant_forced_subtitles.py",
            "report_subtitle_tracks_and_cues.py",
            "set_default_english_text_subtitles.py",
            "subtitle_maintenance/native_worker.py",
            "subtitle_maintenance/bazarr_bridge.py",
        )
        paths = [ROOT / name for name in names]
        with (
            patch("subprocess.Popen", side_effect=AssertionError("subprocess during import")),
            patch("subprocess.run", side_effect=AssertionError("subprocess during import")),
            patch("urllib.request.urlopen", side_effect=AssertionError("network during import")),
            patch(
                "local_credentials.get_secret",
                side_effect=AssertionError("credentials during import"),
            ),
            patch.object(Path, "unlink", side_effect=AssertionError("delete during import")),
            patch.object(Path, "write_text", side_effect=AssertionError("write during import")),
        ):
            for path in paths:
                with self.subTest(path=path.name):
                    load_script(path)

    def test_no_arguments_only_show_help(self):
        for module in [cli] + [
            load_script(ROOT / name)
            for name in ("whisper-subtitles.py", "eastenders-fix.py", "subarr-daily-backup.py")
        ]:
            with (
                self.subTest(module=module.__name__),
                patch.object(module, "load_config") as config,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(module.main([]), 0)
                self.assertIn("usage:", output.getvalue())
                config.assert_not_called()

    def test_invalid_cli_does_not_create_state(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state"
            for extra in (
                ["--limit", "-1"],
                ["--apply", "--scan-only"],
                ["--doctor", "--apply"],
                ["--imdb", "bad"],
            ):
                with (
                    self.subTest(extra=extra),
                    patch.object(cli, "load_config", return_value={}),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    with self.assertRaises(SystemExit):
                        cli.main(["--state-dir", str(state), *extra])
                    self.assertFalse(state.exists())

    def test_doctor_never_runs_tools_or_contacts_services(self):
        with (
            patch("subprocess.Popen", side_effect=AssertionError("execution")),
            patch("urllib.request.urlopen", side_effect=AssertionError("network")),
            patch.object(Path, "mkdir", side_effect=AssertionError("write")),
            patch.object(doctor.shutil, "which", return_value=None),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(doctor.diagnose({}), 1)

    def test_whisper_default_preview_does_not_write_or_call_asr(self):
        module = load_script(ROOT / "whisper-subtitles.py")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            video = root / "sample.mkv"
            video.write_bytes(b"fixture, not media")
            config = {"media_root": d, "whisper": {"state_dir": str(root / "state")}}
            with (
                patch.object(module, "load_config", return_value=config),
                patch.object(module, "tool", return_value="fake"),
                patch.object(module, "has_audio_stream", return_value=True),
                patch.object(module, "has_embedded_subtitle", return_value=False),
                patch.object(module, "check_asr_service") as service,
                patch.object(module, "transcribe") as transcribe,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(module.main(["--path", d, "--ignore-grace"]), 0)
                service.assert_not_called()
                transcribe.assert_not_called()
                self.assertEqual(list(root.iterdir()), [video])

    def test_whisper_state_reset_requires_apply(self):
        module = load_script(ROOT / "whisper-subtitles.py")
        with (
            patch.object(module, "load_config") as config,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit):
                module.main(["--clear-state"])
            config.assert_not_called()

    def test_backup_preview_never_invokes_docker(self):
        module = load_script(ROOT / "subarr-daily-backup.py")
        with (
            patch.object(module, "load_config", return_value={}),
            patch.object(module, "tool", return_value="docker"),
            patch.object(module.subprocess, "run") as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(module.main(["--config", "fixture.json"]), 0)
            run.assert_not_called()

    def test_eastenders_cleanup_needs_both_explicit_permissions(self):
        module = load_script(ROOT / "eastenders-fix.py")
        for apply, cleanup in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(apply=apply, cleanup=cleanup), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                source = root / "EastEnders S2026E134.ts"
                source.write_bytes(b"original")
                replacement = root / "replacement.mkv"
                replacement.write_bytes(b"verified replacement")
                episode = {
                    "id": 1,
                    "seasonNumber": 42,
                    "episodeNumber": 134,
                    "airDate": "2026-09-01",
                    "hasFile": True,
                }

                def get(url, **kwargs):
                    data = (
                        [{"id": 9, "title": "EastEnders"}]
                        if url.endswith("/series")
                        else [episode]
                        if url.endswith("/episode")
                        else {"episodeFile": {"path": "/data/EastEnders/replacement.mkv"}}
                    )
                    return SimpleNamespace(json=lambda: data, raise_for_status=lambda: None)

                config = {
                    "shows": {"eastenders_root": d},
                    "sonarr": {"eastenders_root": "/data/EastEnders"},
                }
                with (
                    patch.object(module, "get_secret", return_value="fixture"),
                    patch("requests.get", side_effect=get),
                    patch("requests.post") as post,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    module.reconcile(config, apply=apply, cleanup_redundant=cleanup)
                    post.assert_not_called()
                self.assertEqual(source.exists(), not (apply and cleanup))
                self.assertEqual(replacement.read_bytes(), b"verified replacement")


class SchemaTests(unittest.TestCase):
    def test_rejects_typos_types_ranges_and_configured_apply(self):
        invalid = [
            {"media_rooot": "/tmp"},
            {"apply": True},
            {"max_downloads": -1},
            {"max_candidates": True},
            {"max_shift": 61},
            {"min_age_minutes": float("nan")},
            {"tools": {"ffmepg": "ffmpeg"}},
            {"backup": {"keep": 0}},
            {"service_url": "file:///tmp"},
            {"service_url": "https://user:password@example.test"},
            {"path_mappings": [["relative", "/container"]]},
            {"human_approved_sha256": ["bad"]},
            {"policy": {"max_candidates": 3}, "max_candidates": 2},
        ]
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate(config)

    def test_policy_normalization_preserves_legacy_settings(self):
        expected = {"max_candidates": 3, "max_downloads": 0}
        self.assertEqual(validate({"policy": expected}), expected)
        self.assertEqual(validate(expected), expected)
