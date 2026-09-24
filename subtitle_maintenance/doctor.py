"""Read-only local setup checks: no media processing, service calls, or model loading."""

import shutil
import subprocess
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
    if shutil.which(runtime):
        try:
            probe = subprocess.run(
                [
                    runtime,
                    "-c",
                    "import importlib.util; raise SystemExit(0 if all(importlib.util.find_spec(n) is not None for n in ('mlx', 'mlx_whisper')) else 1)",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            available = probe.returncode == 0
            message = (
                "available (model not loaded)" if available else "missing or Python probe failed"
            )
        except (OSError, subprocess.TimeoutExpired):
            available = False
            message = "Python probe failed or timed out"
        results.append(
            (
                "OK" if available else ("ERROR" if config.get("model") else "WARN"),
                "MLX packages: " + message,
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
            "Full dialogue repair requires Apple Silicon MLX. The separate Whisper backfill script supports ASR services.",
        )
    )
    return results


def diagnose(config):
    results = checks(config)
    for severity, message in results:
        print(f"{severity}: {message}")
    return 1 if any(severity == "ERROR" for severity, _ in results) else 0
