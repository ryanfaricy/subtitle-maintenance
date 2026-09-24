"""Plan and explicitly confirm installation of the two core media-tool packages."""

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

TOOLS = {
    "ffmpeg": "ffmpeg",
    "ffprobe": "ffmpeg",
    "mkvmerge": "mkvtoolnix",
    "mkvpropedit": "mkvtoolnix",
}


@dataclass
class InstallPlan:
    commands: list[list[str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    blocked: bool = False


def distribution():
    """Read an OS identifier as data; never source a system file in a shell."""
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("ID="):
                return line[3:].strip().strip("\"'")
    except OSError:
        pass
    return ""


def plan_install(config):
    packages = set()
    broken = []
    for tool, package in TOOLS.items():
        configured = config.get("tools", {}).get(tool, tool)
        if shutil.which(configured):
            continue
        if configured != tool:
            broken.append(tool)
        else:
            packages.add(package)
    if broken:
        return InstallPlan(
            notes=[
                "Fix configured tools."
                + ", tools.".join(sorted(broken))
                + " first; installation will not replace custom paths."
            ],
            blocked=True,
        )
    if not packages:
        return InstallPlan(notes=["Core media tools are already available. Nothing to install."])
    packages = sorted(packages)
    if sys.platform == "darwin":
        brew = shutil.which("brew")
        if brew:
            return InstallPlan(commands=[[brew, "install", *packages]])
        return InstallPlan(
            notes=[
                "Homebrew is not installed or not on PATH. Install it yourself from https://brew.sh, then rerun doctor --install."
            ],
            blocked=True,
        )
    if sys.platform == "linux" and distribution() in ("debian", "ubuntu"):
        apt = shutil.which("apt-get")
        prefix = []
        if os.geteuid() != 0:
            sudo = shutil.which("sudo")
            if not sudo:
                return InstallPlan(
                    notes=[
                        "sudo is unavailable. Ask an administrator to install ffmpeg and mkvtoolnix."
                    ],
                    blocked=True,
                )
            prefix = [sudo]
        if apt:
            return InstallPlan(
                commands=[[*prefix, apt, "update"], [*prefix, apt, "install", *packages]]
            )
    return InstallPlan(
        notes=[
            "Automatic installation supports macOS/Homebrew and Debian or Ubuntu/apt only. Install ffmpeg and mkvtoolnix using your system package manager, then rerun doctor."
        ],
        blocked=True,
    )


def install_missing(config):
    plan = plan_install(config)
    for note in plan.notes:
        print(note)
    if plan.blocked:
        return 1
    if not plan.commands:
        return 0
    print(
        "Proposed package-manager commands (network access, disk space, and admin permission may be needed):"
    )
    for command in plan.commands:
        print("  " + shlex.join(command))
    print("Optional Python packages, models, OCR, services, and schedules are not installed here.")
    if not sys.stdin.isatty():
        print(
            "No changes made: rerun in an interactive terminal to review and confirm installation."
        )
        return 2
    try:
        if input("Run these commands? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Cancelled; no installation started.")
            return 0
        for command in plan.commands:
            # No shell, arbitrary packages, repository changes, or automatic retries.
            # sudo/package-manager prompts remain in the user's terminal.
            subprocess.run(command, check=True)
    except (EOFError, KeyboardInterrupt):
        print("\nStopped. If installation started, check package-manager status before retrying.")
        return 130
    except (OSError, subprocess.CalledProcessError) as exc:
        print(
            f"Installation failed ({type(exc).__name__}); it may be partial. Run doctor and review your package manager before retrying."
        )
        return 1
    remaining = plan_install(config)
    for note in remaining.notes:
        print(note)
    if remaining.commands or remaining.blocked:
        print(
            "Some tools are still unavailable. Check PATH and configured tools, then rerun doctor."
        )
        return 1
    return 0
