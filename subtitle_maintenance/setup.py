"""Interactive first-run setup, isolated from media processing and credentials."""

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path

from script_config import config_path, load_config

from .config_schema import validate


def write_new_config(path, config):
    """Atomically publish a private config without overwriting even a dangling symlink."""
    validate(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".subtitle-config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Unlike replace(), link() fails if another process created the target.
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def initialize(argv):
    parser = argparse.ArgumentParser(prog="subtitle-maintain init", description=__doc__)
    parser.add_argument(
        "--config", type=Path, help="New config destination; existing files are preserved"
    )
    args = parser.parse_args(argv)
    target = config_path(args.config)
    if target.exists() or target.is_symlink():
        try:
            load_config(target)
        except ValueError as exc:
            print(f"Existing config left untouched: {exc}")
            return 1
        print(f"Existing config is valid and unchanged: {target}")
        print("Edit it directly, or use --config with a new filename.")
        return 0
    if not sys.stdin.isatty():
        print(
            "init needs an interactive terminal. Copy subtitle-maintenance.example.json for scripted setup."
        )
        return 2
    print(f"New private config: {target}")
    print(
        "Setup creates settings only. No tools, models, credentials, schedules or media are changed."
    )
    try:
        raw_root = input("Media folder: ").strip()
        if not raw_root:
            raise ValueError("A media folder is required")
        root = Path(raw_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("Media folder must be an existing directory")
        native = sys.platform == "darwin" and platform.machine().lower() == "arm64"
        choice = (
            input(
                "Transcription: audit only [audit]"
                + (" or local Apple Silicon MLX [native]" if native else "")
                + ": "
            )
            .strip()
            .lower()
            or "audit"
        )
        if choice not in ("audit", "native"):
            raise ValueError("Choose audit or native")
        if choice == "native" and not native:
            raise ValueError("Native MLX setup requires Apple Silicon macOS; choose audit here")
        config = {
            "media_root": str(root),
            "python": sys.executable,
            "policy": {
                "max_candidates": 3,
                "max_downloads": 20,
                "max_shift": 60,
                "min_age_minutes": 10,
            },
        }
        if choice == "native":
            runtime = (
                input(f"Python with mlx-whisper installed [{sys.executable}]: ").strip()
                or sys.executable
            )
            executable = shutil.which(str(Path(runtime).expanduser()))
            if not executable:
                raise ValueError("Configured Python executable was not found")
            raw_model = input("Existing local MLX model folder (no download will run): ").strip()
            if not raw_model or not Path(raw_model).expanduser().is_dir():
                raise ValueError("Choose an existing local model folder")
            config.update(python=executable, model=str(Path(raw_model).expanduser().resolve()))
        validate(config)
        print(json.dumps(config, indent=2))
        if input(f"Create {target}? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Cancelled; no configuration written.")
            return 0
        write_new_config(target, config)
    except (EOFError, KeyboardInterrupt):
        print("\nSetup interrupted. Check the destination before rerunning.")
        return 130
    except (OSError, ValueError) as exc:
        print(f"Setup failed: {exc}")
        return 1
    print(f"Created {target} with owner-only permissions.")
    print("Next: subtitle-maintain doctor, then subtitle-maintain --scan-only")
    if args.config:
        print("Include --config " + str(target) + " when running those commands.")
    if choice == "audit":
        print(
            "Audit-only setup: use --scan-only or --audit. Full dialogue repair still needs native MLX."
        )
    else:
        print(
            "MLX/model compatibility is not tested by setup. Install Python extras inside a virtual environment."
        )
    return 0
