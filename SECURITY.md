# Local secrets and safe publication

Service credentials must never be committed. `local_credentials.get_secret`
checks the named environment variable first, then
`~/.config/subtitle-maintenance/secrets.json` (owned by the current user, mode
0600). Keep its parent directory mode 0700. The Sonarr scripts use the
`SONARR_API_KEY` key. Missing/unsafe configuration fails before service requests.
Do not import or execute `eastenders-fix.py` to inspect its settings: it performs
live imports and cleanup at module scope. Its existing operational behavior has
not been changed by credential extraction.

`plex-sonarr-import.py --legacy-config ...` still reads literal URL settings
without executing the legacy file, then obtains the migrated key locally.
Environment overrides continue to work. No service key was rotated, and no
LaunchAgent or service configuration was changed.

`subtitle-maintenance.json` remains local and ignored; start from
`subtitle-maintenance.example.json` on a new machine. Fill in local Python/model,
provider bridge and path mappings as described in the main guide. Never commit
runtime transcripts, subtitle downloads, private configuration, reports or backup
archives. `old/` is local archival material and is not part of the published repo.
Ignoring a file does not remove it from Git history.

## Publication review

The first-publication review found one hardcoded Sonarr key. Its historical
occurrences must be redacted as well as its current assignment. Operational CSVs,
local settings, archived tools/backups and superseded script snapshots are removed
from outgoing history, while their on-disk copies and original Git history are
retained in an owner-only backup outside this repository. Commit IDs necessarily
change during this first-publication sanitization. Never push the private backup
or merge its original history back into the published branch.

Before pushing, inspect all outgoing commits and run Gitleaks with redacted output
against their history, plus a scan of the exported tree. Do not disable scanner
rules to make a secret pass. A passing scanner cannot prove that no secret exists:
also inspect credential-loading paths and tracked artifacts. If a credential was
published elsewhere, rotate it at its service; sanitizing Git does not revoke it.
No force-push or remote history replacement is part of this initial publication.
