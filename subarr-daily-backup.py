#!/usr/bin/env python3
"""Create a verified Subarr backup using shared non-secret configuration."""

import argparse
import subprocess
import sys
from pathlib import Path

from script_config import load_config, tool

PAYLOAD = """
import json
import sys
import sqlite3
import time
from pathlib import Path

from subarr.db_integrity import vacuum_backup

backups_dir = Path(sys.argv[2])
today = time.strftime("%Y%m%d")
today_prefix = f"subarr-{today}-"
existing_today = sorted(backups_dir.glob(f"{today_prefix}*.db"))

if existing_today:
    latest = existing_today[-1]
    with sqlite3.connect(latest) as db:
        check = db.execute("PRAGMA integrity_check").fetchone()[0]
    if check != "ok":
        raise RuntimeError(f"existing daily backup failed integrity check: {check}")
    print(json.dumps({
        "integrity_check": check,
        "path": str(latest),
        "skipped": "a verified backup already exists for today",
    }, sort_keys=True))
    raise SystemExit(0)

result = vacuum_backup(
    Path(sys.argv[1]),
    backups_dir,
    when=time.time(),
    keep=int(sys.argv[3]),
)

with sqlite3.connect(result["path"]) as db:
    check = db.execute("PRAGMA integrity_check").fetchone()[0]

if check != "ok":
    raise RuntimeError(f"backup integrity check failed: {check}")

result["integrity_check"] = check
print(json.dumps(result, sort_keys=True))
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--apply", action="store_true", help="Create a backup; otherwise print a preview"
    )
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        parser.print_help()
        return 0
    args = parser.parse_args(arguments)
    try:
        config = load_config(args.config)
        docker = tool(config, "docker")
        settings = config.get("backup", {})
        keep = settings.get("keep", 7)
        if type(keep) is not int or keep < 1:
            raise ValueError("backup.keep must be a positive integer")
    except ValueError as e:
        parser.error(str(e))
    container = settings.get("container", "subarr")
    if not args.apply:
        print(f"Would back up container {container}; use --apply to create a verified backup.")
        return 0
    check = subprocess.run(
        [docker, "inspect", "-f", "{{.State.Running}}", container],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check.returncode or check.stdout.strip() != "true":
        parser.error("Subarr container is not running; backup not created")
    command = [
        docker,
        "exec",
        "--user",
        settings.get("user", "subarr"),
        container,
        settings.get("python", "/usr/local/bin/python"),
        "-c",
        PAYLOAD,
        settings.get("database", "/config/subarr.db"),
        settings.get("directory", "/config/backups"),
        str(keep),
    ]
    return subprocess.run(command, timeout=600).returncode


if __name__ == "__main__":
    raise SystemExit(main())
