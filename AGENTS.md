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
