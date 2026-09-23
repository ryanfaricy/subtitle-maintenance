# Media scripts: active maintenance instructions

Read `SCRIPT_CONTEXT.md`, `subtitle-maintenance-README.md` and
`subtitle-maintenance-MAINTAINING.md` before changing subtitle behavior.

- Public entry point: `subtitle-maintain.py`; implementation: `subtitle_maintenance/`.
- Preview is default. Explicit audits/cleanup never launch OCR, ASR or downloads.
- Do not execute scripts against live media merely to test. Use isolated unit tests.
- Preserve originals, receipts, logs, caches and archival manifests. Never weaken
  verification gates to claim a successful repair. No automatic correctness is 100%.
- Language ambiguity, forced-only content and wrong cuts must remain review cases.
- Keep cleanup opt-in; do not silently add default changes or embedded-track deletion.
- No hardcoded credentials or secret logging. Do not import `eastenders-fix.py`:
  it has module-scope mutations and legacy sensitive configuration.
- Tests: from ~/scripts run `python3 -m unittest discover -s subtitle_maintenance/tests -v`.
- Synchronize installed and development copies. Runtime deps include ffmpeg,
  MKVToolNix, Subtitle Edit, the configured MLX runtime and provider bridge requests.
- Archive only explicitly superseded files after caller checks, with checksums.
  Historical instructions under old/ are not current run instructions.

## Git workflow — standing user preference

For IPTV work that changes scripts in this repository, finish each coherent,
validated change with a local Git commit using Conventional Commit style, e.g.
`fix(subtitles): report provider download deferrals accurately` or
`feat(subtitles): show selected release in preview output`.

- Inspect status and the staged diff first. Stage explicit task-owned paths or
  hunks only; never sweep unrelated changes into a commit or discard them.
- Include corresponding tests, comments and documentation. Run relevant checks
  before committing and report the commit hash and validation result to the user.
- Check for credentials and exclude secrets, machine-local configuration, media,
  logs, caches, generated reports and runtime artifacts from new commits.
- If safe staging or validation is blocked, explain it rather than silently
  skipping the commit or claiming the work was committed.
- Commits are local only. Do not push, amend unrelated commits or rewrite history
  unless separately requested. Preserve this policy when regenerating this file.
