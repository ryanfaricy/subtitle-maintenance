import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from subtitle_maintenance import cli, doctor, native, providers, workflow


class ReviewRegressions(unittest.TestCase):
    def test_provider_uses_configured_docker(self):
        with tempfile.TemporaryDirectory() as d:
            provider = providers.Provider(
                {"bazarr_container": "fixture", "tools": {"docker": sys.executable}}, Path(d)
            )
            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(providers.subprocess, "Popen") as launch,
                patch.object(providers, "read_helper_response", return_value={}),
                patch.object(providers.time, "sleep"),
            ):
                provider.exchange({"action": "search"})
            self.assertEqual(launch.call_args.args[0][0], sys.executable)

    def test_preview_honors_cooldown_without_writing_state(self):
        path = Path(__file__).resolve().parents[2] / "whisper-subtitles.py"
        spec = importlib.util.spec_from_file_location("whisper_review", path)
        whisper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(whisper)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            video = root / "fixture.mkv"
            video.write_bytes(b"fixture")
            os.utime(video, (time.time() - 90000, time.time() - 90000))
            whisper.STATE_DIR = root / "state"
            whisper.STATE_DB = whisper.STATE_DIR / "state.db"
            db = whisper.connect_db()
            size, mtime = whisper.fingerprint(video)
            whisper.save_state(
                db, video, size, mtime, time.time(), "failed", failure_time=time.time()
            )
            db.close()
            original = whisper.STATE_DB.read_bytes()
            config = root / "config.json"
            config.write_text(json.dumps({"whisper": {"state_dir": str(whisper.STATE_DIR)}}))
            output = io.StringIO()
            with (
                patch.object(whisper, "tool", return_value=sys.executable),
                patch.object(whisper, "has_audio_stream", return_value=True) as probe,
                patch.object(whisper, "has_embedded_subtitle", return_value=False),
                patch.object(whisper, "transcribe") as transcribe,
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(
                    whisper.main(["--path", str(video), "--config", str(config), "--dry-run"]), 0
                )
            self.assertNotIn("WOULD TRANSCRIBE", output.getvalue())
            probe.assert_not_called()
            transcribe.assert_not_called()
            self.assertEqual(original, whisper.STATE_DB.read_bytes())

    def test_doctor_reports_missing_packages_and_timeout(self):
        for result in [subprocess.CompletedProcess([], 1), subprocess.TimeoutExpired("python", 10)]:
            with (
                self.subTest(result=result),
                patch.object(doctor.shutil, "which", return_value=sys.executable),
                patch.object(doctor.subprocess, "run") as run,
            ):
                if isinstance(result, Exception):
                    run.side_effect = result
                else:
                    run.return_value = result
                checks = doctor.checks({"python": sys.executable, "model": "org/model"})
                self.assertTrue(
                    any(
                        level == "ERROR" and text.startswith("MLX packages:")
                        for level, text in checks
                    )
                )
                self.assertEqual(run.call_args.kwargs["timeout"], 10)

    def test_doctor_detects_packages_without_importing_them(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ["mlx", "mlx_whisper"]:
                (Path(d) / (name + ".py")).write_text('raise RuntimeError("must not import")')
            with (
                patch.dict(os.environ, {"PYTHONPATH": d}),
                patch.object(doctor.shutil, "which", return_value=sys.executable),
            ):
                checks = doctor.checks({"python": sys.executable, "model": "org/model"})
            self.assertTrue(
                any(level == "OK" and text.startswith("MLX packages:") for level, text in checks)
            )

    def test_native_failure_is_typed_and_cache_miss_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            video = root / "test.mkv"
            video.write_bytes(b"fixture")
            info = {"audio_index": 0, "data": {"streams": []}}
            with self.assertRaisesRegex(native.TranscriptionError, "not configured"):
                native.transcript(video, info, None, None, {"model": ""}, root)
            with self.assertRaises(native.TranscriptCacheMiss):
                native.transcript(video, info, None, None, {"model": "", "cache_only": True}, root)

    def test_sidecar_failure_does_not_download_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            video = root / "fixture.mkv"
            video.write_bytes(b"video")
            source = root / "fixture.en.srt"
            source.write_text("subtitle")
            info = dict(
                text=[],
                bitmap=[],
                audio_index=0,
                data={"streams": [{"index": 0, "codec_type": "audio"}]},
            )
            args = SimpleNamespace(
                audio_stream=None, scan_only=False, apply=False, no_download=False
            )
            provider = MagicMock()
            with (
                patch.object(workflow.media, "inventory", return_value=info),
                patch.object(
                    workflow, "prepare", side_effect=native.TranscriptionError("worker failed")
                ),
            ):
                with self.assertRaises(native.TranscriptionError):
                    workflow.process(video, args, {"min_age_minutes": 0}, root, provider)
            provider.candidates.assert_not_called()
            provider.download.assert_not_called()
            self.assertEqual(source.read_text(), "subtitle")

    def test_cli_saves_error_and_stops_remaining_videos(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ["a.mkv", "b.mkv"]:
                (root / name).write_bytes(b"fixture")
            config = root / "config.json"
            config.write_text("{}")
            state = root / "state"
            with (
                patch.object(
                    cli, "process", side_effect=native.TranscriptionError("worker failed")
                ) as process,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = cli.main([str(root), "--config", str(config), "--state-dir", str(state)])
            self.assertEqual(code, 1)
            self.assertEqual(process.call_count, 1)
            report = json.loads((state / "latest-report.json").read_text())
            self.assertEqual(report["counts"], {"ERROR": 1})
            self.assertIn("worker failed", report["results"][0]["error"])

    def test_provider_failure_stops_before_second_download(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            video = root / "fixture.mkv"
            video.write_bytes(b"fixture")
            info = dict(
                text=[],
                bitmap=[],
                audio_index=0,
                data={"streams": [{"index": 0, "codec_type": "audio"}]},
            )
            args = SimpleNamespace(
                audio_stream=None, scan_only=False, apply=False, no_download=False, identity=None
            )
            provider = MagicMock()
            provider.candidates.return_value = [{"release": "first"}, {"release": "second"}]
            with (
                patch.object(workflow.media, "inventory", return_value=info),
                patch.object(workflow, "identity", return_value={}),
                patch.object(
                    workflow, "prepare", side_effect=native.TranscriptionError("worker failed")
                ),
            ):
                with self.assertRaises(native.TranscriptionError):
                    workflow.process(video, args, {"min_age_minutes": 0}, root, provider)
            self.assertEqual(provider.download.call_count, 1)
            self.assertFalse(video.with_suffix(".en.srt").exists())
