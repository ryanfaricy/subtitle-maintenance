# Active script context — 2026-09-23

## Guided setup commands

`init` creates a validated private config only after confirmation, preserving
existing files. `doctor --install` proposes allowlisted Homebrew or Debian/Ubuntu
apt commands, requires interactive confirmation, and verifies installed tools.
Neither config setup nor ordinary doctor processes media or enables schedules.
Tests mock installers and use temporary config files; never invoke a real
package-manager installation merely for testing.


## Version 0.2 safety changes

Bare entry points now show help. Whisper generation and Subarr backup require
`--apply`; update scheduled invocations explicitly. EastEnders is safe to import,
previews by default, and requires `--apply --cleanup-redundant` for source deletion.
See [README.md](README.md) for setup, config validation, packaging and migration.


## Shared configuration and portable installation

See [README.md](README.md) for clone setup and shared settings. The loader now
finds `subtitle-maintenance.json` beside the scripts, supports `--config` and
`SUBTITLE_MAINTENANCE_CONFIG`, and resolves relative host paths against that file.
Media/show roots, standalone Whisper tools/service, Sonarr paths and Subarr
backup settings no longer require source edits. Private installed settings and
existing state locations are preserved. No verification gates were changed.


Ryan wants full-dialogue English subtitles for Tunarr/Plex. Embedded English text
is trusted by policy; embedded bitmap can be OCRed to text while retaining bitmap.
Sidecars need dialogue/timing verification; wrong-cut QI vs QI XL subtitles cannot
be repaired merely by changing an offset. Preserve human-approved tracks.

Use `subtitle-maintain.py` and its README for normal work. Configuration is in
`subtitle-maintenance.json`. Default state, reports and backups live under
`~/Library/Application Support/SubtitleMaintenance`. No service scheduling was
changed by consolidation. The provider path still uses Bazarr's credential bridge
unless standalone credentials are configured; do not retire Bazarr yet.

Downloaded subtitle embedding remains disabled: automated checks cannot satisfy
the requested 100% certainty. OCR/container verification is not text proofreading.
Live consolidated OCR and movie paths still need controlled pilots before broad use.

`subtitle-maintenance-MAINTAINING.md` maps modules, original scripts, deliberate
exceptions, safety contracts and tests. Forced/default editing, QI-specific language
repair, cue reports, Whisper generation and unrelated service utilities remain
separate. They are not implicitly run by the main command.

Historical full context and old AGENTS instructions are retained in dated old/
archives alongside checksum manifests. They contain useful historical service/cache
details, but their old executable paths must not be treated as current commands.
No logs, report CSVs, OCR runtimes, original backups or service configurations were
removed. Restore an archived tool only after reviewing its cross-dependencies.
