#!/bin/zsh
set -euo pipefail
# Keep existing scheduled callers working; all settings live in shared JSON.
exec python3 "${0:A:h}/subarr-daily-backup.py" "$@"
