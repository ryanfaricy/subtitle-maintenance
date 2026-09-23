# Maintenance and migration map

## Active architecture

`subtitle-maintain.py` is the public entry point; `subtitle_maintenance/cli.py`
owns argument validation, run locking, state and reports. `workflow.py` routes
normal repair; `media.py` owns language/format classification; `subtitles.py`
and `validation.py` own dialogue gates; `native.py` owns speech/cache/resource
limits; `providers.py` and `bazarr_bridge.py` isolate provider access;
`common.py` owns backup/install primitives. `housekeeping.py` implements explicit
audits and sidecar quarantine without speech recognition or provider calls.
`bitmap_ocr.py` is a retained legacy engine, called on a copy by the workflow.

Do not conflate metadata trust, container verification and dialogue verification.
None proves 100% correctness. Do not loosen verification to make failures pass.
Keep forced-only tracks distinct from full dialogue. Unknown language needs
review. English bitmap is not a replacement for English text during cleanup.

## Consolidated in this follow-up

| Previous script | Replacement / deliberate difference |
| --- | --- |
| audit_missing_english_text_subtitles.py | `--audit coverage`; explicit language only, SRT/ASS/SSA/VTT sidecars; other formats require review |
| cleanup_redundant_subtitle_sidecars.py | `--cleanup-sidecars`; preview default, checksum quarantine/restore; no unlabelled sidecar removal |
| remove_redundant_subtitles.py | Same cleanup; bitmap/forced embedded tracks no longer qualify |
| Four `.py.pre-*` snapshots | Historical only; archived, not executable dependencies |

`--audit defaults` adds an integrated metadata overview. Keep
`audit_default_english_subtitles.py` because the specialized default setter still
consumes its CSV schema; the JSON overview is NOT a replacement for that schema.

## Deliberately separate

- `set_default_english_text_subtitles.py` and its CSV audit: explicit default
  selection changes playback policy; not part of normal repair.
- `audit_redundant_forced_subtitles.py`, `remove_redundant_forced_subtitles.py`,
  `report_subtitle_tracks_and_cues.py`: specialized review/removal pipeline,
  including packet metrics and cue previews. No automatic forced-track deletion.
- `fix_qi_english_subtitle_language.py`: show-specific assumptions; do not apply
  its relabelling policy across the library.
- `whisper-subtitles.py`: generation/backfill, not verified provider repair.
- `eastenders-fix.py`, `plex-sonarr-import.py`, `subarr-daily-backup.sh`: unrelated
  operations; do not import/run as tests. EastEnders has module-scope mutations.
- `seconv-5.2.0/`: live OCR runtime dependency, not archive material.
- Logs, CSV reports, backups and state remain where they were.

## Quarantine contract and recovery

Cleanup is a separate mode, never an implicit side effect of normal repair.
It requires a full non-forced English text stream, an explicitly English text
sidecar and no same-stem alternative video. Embedded text is trusted, not newly
dialogue-verified. The exact source is copied to a content-addressed backup,
hashed, journalled PREPARED, checked again, then unlinked. QUARANTINED means the
sidecar is no longer beside the video; the backup remains indefinitely.
Receipts live in the state's `quarantine/` directory. PREPARED receipts can be
recovered after interruption if the target is absent and the backup hash matches.
Restore refuses existing targets, including symlinks. Stop other subtitle writers
before cleanup: the app lock does not lock Bazarr/Subarr/FileFlows. Tiny external
write races remain possible between checking and unlinking. This is not a shared
filesystem transaction. Never delete quarantine objects merely to tidy up.

## Development checklist

Run `python3 -m unittest discover -s tests -v` in the development checkout, or
`python3 -m unittest discover -s subtitle_maintenance/tests -v` from ~/scripts.
Tests use temporary fixtures; live media writes need explicit authorization.
Keep the development checkout and installed package synchronized. Add tests for
matching, changed files, rollback, interruption and wrong cuts when changing them.
Provider credentials must remain runtime-only; never log or put them in fixtures.
New subprocesses require stdin isolation, bounded timeout and resource limits.
New archive batches need exact names, SHA256 manifest and caller checks. Restore
archived scripts by copying, not deleting their archival originals. Older groups
have cross-dependencies: restore a coherent group and review paths before use.
