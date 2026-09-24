# Maintenance and migration map

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


## Active architecture

Dialogue scoring excludes explicit ASS drawing-mode spans and conservatively
recognized complete vector paths with retained positioning tags in SRT exports.
Mixed cues keep their spoken text; ambiguous number/letter sequences remain text.
Only confirmed drawing-only cues are excluded from the phrase coverage denominator.
Cue text, timestamps and rendering markup are never edited by this normalization.
`DIALOGUE_SCORING_VERSION` invalidates result caches, and preservation policy 2
reconsiders prior decisions. Raw transcription caches remain reusable. Do not
strip all numeric text or all positioned cues; those can contain real dialogue.

`preservation.py` is an opt-in unchanged-SRT retention policy, not a replacement
validation gate. It aligns exact normalized words in order with SequenceMatcher
(autojunk disabled), retaining all subtitle words in the denominator, including
short cues. Require 95% subtitle-word agreement, 95% matched-word timing within
the display interval +/-1.5s, 90% audio-word coverage, 200 word matches, 100 unique
phrase anchors with 80% span, and ten regional 90%/90% checks with 20 words each.
These are ASR agreement measurements, never calibrated confidence percentages.
Keep raw originals unchanged and recheck video fingerprint/sidecar hash before
returning KEPT_EXISTING. Only existing SRTs enter this path. Never pass downloaded
or retimed candidates through it. Inspect every existing SRT before repair to
avoid replacing one when another already meets policy. Bump POLICY_VERSION when
threshold/measurement semantics change; the CLI includes it in the cache key.
The regular installation/independent-sample gates are unchanged. Failed searches
now report existing-versus-missing availability separately from verification.

`drift.py` implements opt-in global affine timing correction, not piecewise
alignment. Fit cue/phrase midpoints using a robust median of separated-pair
slopes, then a median intercept. Exclude anchor phrases overlapping the five
independent sample windows (5/15/50/85/95 percent). Require 100 anchors, 65% span,
scale 0.95–1.05 (at least 0.001 from unity), intercept within 60s, median residual
<=0.5s and p90 <=1.5s, plus ten anchors and median signed residual <=0.75s per
fifth. These are proposal gates only: unchanged full_check and all five sample
checks authorize the candidate. No second shift is permitted. Revalidate the
serialized output against the same five samples with fitting disabled. Skip
combined SxxExx-Eyy files; do not use drift to bypass a passed-full/failed-sample
result. Fit details and rejection evidence belong in per-candidate reports.
Tests cover synthetic scale recovery, sparse/extreme/piecewise rejection,
combined-episode exclusion, option isolation, text preservation and render gates.
Real-media preview review remains required before broad apply of this trial.

`subtitle-maintain.py` is the public entry point; `subtitle_maintenance/cli.py`
owns argument validation, run locking, state and reports. `workflow.py` routes
normal repair; `media.py` owns language/format classification; `subtitles.py`
and `validation.py` own dialogue gates; `native.py` owns speech/cache/resource
limits; `providers.py` and `bazarr_bridge.py` isolate provider access;
`common.py` owns backup/install primitives. `housekeeping.py` implements explicit
audits and sidecar quarantine without speech recognition or provider calls.
`bitmap_ocr.py` is a retained legacy engine, called on a copy by the workflow.

Provider helper IPC uses binary os.read with one total response deadline, avoiding
TextIO buffering/select mismatches and blocking readline on partial responses.
Discard/reap a failed helper so delayed replies cannot satisfy another request.
Search transport retries are capped at one; never replay an uncertain download.
Diagnostics retain exception class, action and process status, not arbitrary
exception text/response bodies. `transport_recovery_version=1` marks modern
cooldowns; only exact legacy generic transport failures are bypassable.

The bridge's `authenticated_download` refreshes only its private bearer once for
download-API HTTP 401. Login never carries the old bearer; content fetches never
carry API credentials. Do not retry download issuance for content/quota/429/server
failures because that may consume quota twice. Cooldown schema
`auth_recovery_version=1` distinguishes post-recovery failures from legacy invalid
token cooldowns. Only that exact legacy case may be bypassed, never rate limits.

`episode_mapping.py` resolves opt-in title matches in the IMDb-confirmed TVmaze
catalog. Require a unique normalized title; never guess with a global season
offset. Preserve local/mapped identities in reports, and never treat catalog
agreement as subtitle validation. Search-cache keys intentionally omit the new
title field to preserve existing caches; mapped season/episode values change the
key. The option is part of the result-cache configuration fingerprint.

`search_both` unions original and mapped queries (one query when identical).
`merge_candidates` deduplicates by file ID and ranks recognized matching titles
before untitled releases. Reject only recognized other catalog titles following
an episode marker, using longest normalized prefix and a minimum eight-character
title. Keep unknown releases eligible. Filter before the candidate cap, record
rejections/provenance, and keep dialogue gates unchanged. Partial search failures
must remain visible even if candidates from the surviving search can be tested.

`audio_tags.py` owns explicit single-untagged-audio MKV tagging, copy verification
and original backup. The opt-in runtime assumption lives in media/workflow and
must not override a known non-English language or ambiguous multiple tracks.
Neither path is automatic spoken-language detection. `DownloadBudgetReached`
is a typed local deferral; keep it separate from failed verification/provider
errors, and continue checking cached candidates even when the cap is exhausted.

`media.choose_audio` resolves multiple tagged English dialogue tracks by default
flag, channel count, then stream index. Exclude commentary/descriptive titles AND
dispositions. Unknown language does not qualify through this rule. Transcript
cache keys include the chosen audio index; bump the application version when
selection policy changes so result caches are reconsidered. No playback settings
are mutated by analysis selection.

Candidate-ranking trial: short exact target titles at token boundaries are
positive evidence only. SequenceMatcher >=0.92 on normalized titles of at least
12 characters (after stripping recognized release tags) provides a middle ranking
tier, never exclusion/acceptance. Exact known other long catalog titles still
take precedence. `--max-candidates` is a positive per-video attempt bound; retain
the independent global download cap and report remaining unattempted candidates.

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

## Executable path identity

Config expansion must preserve executable symlinks, especially virtualenv Python.
Resolving the Python symlink to its base installation bypasses the environment's
packages and breaks uncached native transcription. The configuration regression
test launches an isolated virtualenv Python and checks its actual runtime prefix.
