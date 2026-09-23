#!/bin/zsh
set -euo pipefail

docker_bin="/usr/local/bin/docker"
container_name="subarr"

if [[ ! -x "$docker_bin" ]]; then
  print -u2 "Docker CLI not found: $docker_bin"
  exit 1
fi

if [[ "$($docker_bin inspect -f '{{.State.Running}}' "$container_name" 2>/dev/null)" != "true" ]]; then
  print -u2 "Subarr container is not running; backup not created"
  exit 1
fi

$docker_bin exec --user subarr "$container_name" /usr/local/bin/python -c '
import json
import sqlite3
import time
from pathlib import Path

from subarr.db_integrity import vacuum_backup

backups_dir = Path("/config/backups")
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
    Path("/config/subarr.db"),
    backups_dir,
    when=time.time(),
    keep=7,
)

with sqlite3.connect(result["path"]) as db:
    check = db.execute("PRAGMA integrity_check").fetchone()[0]

if check != "ok":
    raise RuntimeError(f"backup integrity check failed: {check}")

result["integrity_check"] = check
print(json.dumps(result, sort_keys=True))
'
