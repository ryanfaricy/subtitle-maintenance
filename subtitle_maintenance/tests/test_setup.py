"""First-run and installer contracts; never install packages or access real media."""

import contextlib
import io
import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import script_config
from subtitle_maintenance import cli, dependencies, setup


class InitTests(unittest.TestCase):
    def test_audit_setup_private_valid_and_rerunnable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "settings/config.json"
            with (
                patch.object(setup.sys.stdin, "isatty", return_value=True),
                patch("builtins.input", side_effect=[str(root), "", "y"]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cli.main(["init", "--config", str(target)]), 0)
            contents = target.read_bytes()
            loaded = script_config.load_config(target)
            self.assertEqual(loaded["media_root"], str(root))
            self.assertNotIn("model", loaded)
            self.assertNotIn("apply", loaded)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            with (
                patch(
                    "builtins.input", side_effect=AssertionError("must preserve existing config")
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(setup.initialize(["--config", str(target)]), 0)
            self.assertEqual(target.read_bytes(), contents)

    def test_decline_eof_and_invalid_root_write_nothing(self):
        for answers in (["", "audit", "y"], ["/does/not/exist", "audit", "y"], None, "decline"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "new/config.json"
                values = [str(root), "audit", "n"] if answers == "decline" else answers
                with (
                    patch.object(setup.sys.stdin, "isatty", return_value=True),
                    patch("builtins.input", side_effect=EOFError if values is None else values),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    setup.initialize(["--config", str(target)])
                self.assertFalse(target.parent.exists())

    def test_noninteractive_refuses_before_prompt(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(setup.sys.stdin, "isatty", return_value=False),
            patch("builtins.input") as prompt,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                setup.initialize(["--config", str(Path(directory) / "config.json")]), 2
            )
            prompt.assert_not_called()

    def test_invalid_existing_config_and_symlink_are_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "config.json"
            target.write_text("broken")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(setup.initialize(["--config", str(target)]), 1)
            self.assertEqual(target.read_text(), "broken")
            target.unlink()
            target.symlink_to(root / "missing")
            with self.assertRaises(FileExistsError):
                setup.write_new_config(target, {})
            self.assertTrue(target.is_symlink())
            self.assertFalse((root / "missing").exists())

    def test_atomic_create_refuses_a_racing_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            target.write_text("other writer")
            with self.assertRaises(FileExistsError):
                setup.write_new_config(target, {})
            self.assertEqual(target.read_text(), "other writer")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_native_setup_and_platform_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            model = root / "model"
            model.mkdir()
            for system, machine, expected in [("darwin", "arm64", 0), ("linux", "x86_64", 1)]:
                target = root / (system + ".json")
                with (
                    patch.object(setup.sys, "platform", system),
                    patch.object(setup.platform, "machine", return_value=machine),
                    patch.object(setup.sys.stdin, "isatty", return_value=True),
                    patch(
                        "builtins.input", side_effect=[str(root), "native", "", str(model), "yes"]
                    ),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(setup.initialize(["--config", str(target)]), expected)
                self.assertEqual(target.exists(), expected == 0)
                if target.exists():
                    self.assertEqual(json.loads(target.read_text())["model"], str(model))

    def test_default_destination_matches_loader_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(script_config, "REPO_ROOT", root),
                patch.object(script_config, "user_config_path", return_value=root / "user.json"),
                patch.dict(script_config.os.environ, {}, clear=True),
            ):
                self.assertEqual(script_config.config_path(), root / "user.json")
                (root / "subtitle-maintenance.json").write_text("{}")
                self.assertEqual(script_config.config_path(), root / "subtitle-maintenance.json")
                with patch.dict(
                    script_config.os.environ, {script_config.CONFIG_ENV: str(root / "env.json")}
                ):
                    self.assertEqual(script_config.config_path(), root / "env.json")
                    self.assertEqual(
                        script_config.config_path(root / "explicit.json"), root / "explicit.json"
                    )


class InstallerTests(unittest.TestCase):
    def test_homebrew_deduplicates_packages_and_skips_installed(self):
        def which(name):
            return {
                "brew": "/opt/homebrew/bin/brew",
                "ffmpeg": "/bin/ffmpeg",
                "ffprobe": "/bin/ffprobe",
            }.get(name)

        with (
            patch.object(dependencies.sys, "platform", "darwin"),
            patch.object(dependencies.shutil, "which", side_effect=which),
        ):
            self.assertEqual(
                dependencies.plan_install({}).commands,
                [["/opt/homebrew/bin/brew", "install", "mkvtoolnix"]],
            )

    def test_debian_ubuntu_and_root_plans(self):
        for distro in ("debian", "ubuntu"):
            for uid in (0, 501):
                with (
                    patch.object(dependencies.sys, "platform", "linux"),
                    patch.object(dependencies, "distribution", return_value=distro),
                    patch.object(dependencies.os, "geteuid", return_value=uid),
                    patch.object(
                        dependencies.shutil,
                        "which",
                        side_effect=lambda name: (
                            "/usr/bin/" + name if name in ("sudo", "apt-get") else None
                        ),
                    ),
                ):
                    plan = dependencies.plan_install({})
                    prefix = ["/usr/bin/sudo"] if uid else []
                    self.assertEqual(
                        plan.commands,
                        [
                            prefix + ["/usr/bin/apt-get", "update"],
                            prefix + ["/usr/bin/apt-get", "install", "ffmpeg", "mkvtoolnix"],
                        ],
                    )

    def test_unsupported_missing_manager_and_broken_override_block(self):
        for system, config in [
            ("linux", {}),
            ("darwin", {}),
            ("darwin", {"tools": {"ffmpeg": "/bad/custom"}}),
        ]:
            with (
                patch.object(dependencies.sys, "platform", system),
                patch.object(dependencies, "distribution", return_value="fedora"),
                patch.object(dependencies.shutil, "which", return_value=None),
            ):
                plan = dependencies.plan_install(config)
                self.assertTrue(plan.blocked)
                self.assertEqual(plan.commands, [])

    def test_decline_and_noninteractive_never_run(self):
        plan = dependencies.InstallPlan(commands=[["brew", "install", "ffmpeg"]])
        for tty, answer in [(True, "n"), (True, ""), (False, "yes")]:
            with (
                patch.object(dependencies, "plan_install", return_value=plan),
                patch.object(dependencies.sys.stdin, "isatty", return_value=tty),
                patch("builtins.input", return_value=answer),
                patch.object(dependencies.subprocess, "run") as run,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(dependencies.install_missing({}), 0 if tty else 2)
                run.assert_not_called()

    def test_confirmed_install_rechecks_tools_without_shell(self):
        plan = dependencies.InstallPlan(commands=[["/brew", "install", "ffmpeg"]])
        with (
            patch.object(
                dependencies, "plan_install", side_effect=[plan, dependencies.InstallPlan()]
            ) as check,
            patch.object(dependencies.sys.stdin, "isatty", return_value=True),
            patch("builtins.input", return_value="yes"),
            patch.object(dependencies.subprocess, "run") as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(dependencies.install_missing({}), 0)
            run.assert_called_once_with(["/brew", "install", "ffmpeg"], check=True)
            self.assertEqual(check.call_count, 2)

    def test_failure_stops_before_next_command(self):
        plan = dependencies.InstallPlan(
            commands=[["apt-get", "update"], ["apt-get", "install", "ffmpeg"]]
        )
        with (
            patch.object(dependencies, "plan_install", return_value=plan),
            patch.object(dependencies.sys.stdin, "isatty", return_value=True),
            patch("builtins.input", return_value="y"),
            patch.object(
                dependencies.subprocess,
                "run",
                side_effect=subprocess.CalledProcessError(1, "apt-get"),
            ) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(dependencies.install_missing({}), 1)
            self.assertEqual(run.call_count, 1)

    def test_cli_install_only_allowed_for_doctor_and_doctor_stays_read_only(self):
        with (
            patch.object(cli, "load_config", return_value={}),
            patch("subtitle_maintenance.doctor.diagnose", return_value=0) as check,
            patch.object(dependencies, "install_missing", return_value=0) as install,
        ):
            self.assertEqual(cli.main(["doctor"]), 0)
            install.assert_not_called()
            self.assertEqual(cli.main(["doctor", "--install"]), 0)
            self.assertEqual(cli.main(["--doctor", "--install"]), 0)
            self.assertEqual(install.call_count, 2)
            check.assert_called_once()
            for args in (
                ["--install"],
                ["doctor", "--install", "--apply"],
                ["doctor", "--install", "--scan-only"],
            ):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    cli.main(args)
