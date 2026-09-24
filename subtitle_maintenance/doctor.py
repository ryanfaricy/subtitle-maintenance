"""Read-only local setup checks: never run tools, contact services, or create state."""

import shutil
import sys
from pathlib import Path


def checks(config):
    """Return severity/message pairs without disclosing credentials or URLs."""
    results = []
    for name in ("ffmpeg", "ffprobe", "mkvmerge", "mkvpropedit"):
        found = shutil.which(config.get("tools", {}).get(name, name))
        results.append(
            (
                "OK" if found else "ERROR",
                f"{name}: "
                + ("available" if found else "install on PATH or configure tools." + name),
            )
        )
    root = config.get("media_root")
    results.append(
        (
            "OK" if root and Path(root).is_dir() else "WARN",
            "media_root: "
            + (
                "available"
                if root and Path(root).is_dir()
                else "configure an existing folder or pass a path explicitly"
            ),
        )
    )
    runtime = config.get("python", sys.executable)
    results.append(
        (
            "OK" if shutil.which(runtime) else "ERROR",
            "transcription Python: " + ("available" if shutil.which(runtime) else "not found"),
        )
    )
    model = config.get("model")
    model_exists = bool(model) and (
        not model.startswith(("/", ".", "~")) or Path(model).expanduser().exists()
    )
    results.append(
        (
            "OK" if model_exists else "WARN",
            "transcription model: "
            + (
                "configured (not loaded)"
                if model_exists
                else "not configured or local path missing"
            ),
        )
    )
    results.append(
        (
            "INFO",
            "Optional OCR needs seconv; provider access needs credentials. Neither is contacted or tested here.",
        )
    )
    results.append(
        (
            "INFO",
            "Native MLX transcription requires Apple Silicon; service-based transcription depends on your configured service.",
        )
    )
    return results


def diagnose(config):
    results = checks(config)
    for severity, message in results:
        print(f"{severity}: {message}")
    return 1 if any(severity == "ERROR" for severity, _ in results) else 0
