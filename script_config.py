"""Shared non-secret settings for the media scripts (standard library only)."""

import json
import os
import shutil
import sys
from pathlib import Path

CONFIG_ENV = "SUBTITLE_MAINTENANCE_CONFIG"
REPO_ROOT = Path(__file__).resolve().parent


def user_config_path():
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        / "subtitle-maintenance/config.json"
    )


def config_path(filename=None):
    """Use the same destination for discovery and init; existing repo config wins."""
    selected = filename or os.environ.get(CONFIG_ENV)
    if selected:
        return Path(selected).expanduser()
    candidate = REPO_ROOT / "subtitle-maintenance.json"
    if candidate.exists() or candidate.is_symlink():
        return candidate
    return user_config_path()


def load_config(filename=None):
    """Explicit paths must exist; an absent default config means portable defaults."""
    path = config_path(filename)
    if not path.exists() and not path.is_symlink() and not (filename or os.environ.get(CONFIG_ENV)):
        return {}
    try:
        config = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read config {path}: {type(exc).__name__}") from None
    from subtitle_maintenance.config_schema import validate

    config = validate(config)
    if not isinstance(config, dict):
        raise ValueError("Config must be a JSON object")
    for key in ("whisper", "sonarr", "shows", "tools", "backup"):
        if key in config and not isinstance(config[key], dict):
            raise ValueError(f"Config {key} must be an object")
    for section, keys in (
        (config, ("service_url",)),
        (config.get("whisper", {}), ("service_url", "model")),
        (config.get("sonarr", {}), ("url", "sonarr_root", "eastenders_root")),
        (config.get("backup", {}), ("container", "user", "python", "database", "directory")),
    ):
        for key in keys:
            if key in section and (not isinstance(section[key], str) or not section[key].strip()):
                raise ValueError(f"Config {key} must be a nonempty string")

    # Resolve known filesystem settings relative to the config, never the caller's cwd.
    def expand(section, keys):
        for key in keys:
            if key in section and section[key] is not None:
                value = section[key]
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"Config {key} must be a nonempty path string")
                p = Path(value).expanduser()
                section[key] = str((path.resolve().parent / p).resolve())

    expand(config, ("media_root", "state_dir", "legacy_transcripts", "bazarr_database"))
    expand(config.get("whisper", {}), ("state_dir",))
    expand(config.get("shows", {}), ("qi_root", "eastenders_root"))
    expand(config.get("sonarr", {}), ("host_root", "work_dir"))
    # Executable names and remote model IDs remain valid; only explicit paths expand.
    for section, keys in (
        (config, ("python", "model")),
        (config.get("tools", {}), tuple(config.get("tools", {}))),
    ):
        for key in keys:
            value = section.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"Config {key} must be a nonempty string")
            if value and (value.startswith(("~", ".", "/"))):
                expand(section, (key,))
    return config


def state_path(config, name="SubtitleMaintenance"):
    value = config.get("state_dir")
    if value:
        return Path(value)
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support" / name
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / name


def tool(config, name):
    value = config.get("tools", {}).get(name, name)
    found = shutil.which(value)
    if not found:
        raise ValueError(
            f"{name} executable not found; install it on PATH or set tools.{name} in config"
        )
    return found


def show_path(config, name):
    explicit = config.get("shows", {}).get(name.lower() + "_root")
    if explicit:
        return Path(explicit)
    root = config.get("media_root")
    if not root:
        raise ValueError(f"Set media_root or shows.{name.lower()}_root in config")
    return Path(root) / "TV" / name
