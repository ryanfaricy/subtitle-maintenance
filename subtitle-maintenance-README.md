# Subtitle Maintain

One entry point for conservative English subtitle maintenance. Preview is the
default. Preview may download candidates and transcribe audio into the state
folder, but never replaces media. `--scan-only` is an inexpensive inventory with
no speech recognition or provider downloads.

## Commands

Metadata-only audits and optional cleanup (no downloads or transcription):

```sh
~/scripts/subtitle-maintain.py "/Volumes/Media/Plex" --audit coverage
~/scripts/subtitle-maintain.py "/Volumes/Media/Plex" --audit defaults
~/scripts/subtitle-maintain.py "/Volumes/Media/Plex" --cleanup-sidecars
# Only after reviewing the preview; stop other subtitle writers first:
~/scripts/subtitle-maintain.py "/Volumes/Media/Plex" --cleanup-sidecars --apply
~/scripts/subtitle-maintain.py --restore-quarantine "/path/to/receipt.json"
```

Restore also requires `--apply` to act. Cleanup quarantines explicitly English
SRT/ASS/SSA/VTT sidecars only when embedded non-forced English text exists.
It never removes bitmap tracks or forced/unknown-language sidecars. Shared-stem
video versions are excluded. Reports are in the state folder's `latest-report.json`
(overwritten each run); recovery receipts/backups remain in `quarantine/`.
The coverage audit is metadata-only, not a timing or dialogue quality guarantee.
See `subtitle-maintenance-MAINTAINING.md` for provenance, exceptions and recovery.

```sh
~/scripts/subtitle-maintain.py "/Volumes/Media/Plex/TV/QI" --scan-only
~/scripts/subtitle-maintain.py "/Volumes/Media/Plex/TV/QI" --limit 5
~/scripts/subtitle-maintain.py "/path/to/video.mkv" --apply
```

Pass one or more files/folders; folders recurse. Progress prints immediately per
file and phase. Re-running resumes via the state database, cached transcripts,
and provider downloads. `--rescan` rechecks previously verified files. A provider
failure opens a cooldown but does not erase progress. The default run budget is
20 new downloads, with up to three candidates per video. No background scheduler
is installed, and no Bazarr/Subarr/FileFlows configuration is changed.

## Routing and safeguards

- Full, non-forced embedded English text: TRUSTED_EMBEDDED, not dialogue-verified.
- Full English embedded bitmap: preview WOULD_OCR; apply runs OCR on an isolated
  copy, keeps original bitmap tracks and adds text. MKV only. The original video
  is backed up and new stream inventory/duration checked before replacement.
  Container checks cannot guarantee OCR text accuracy. This pathway inherits the
  previously used Subtitle Edit worker and should be piloted on copies after any
  worker/tool upgrade. It may require about three video sizes of free media space
  plus another video size for the retained backup. Existing sidecars stay.
- English sidecars: dialogue/display-interval verification across ten timeline
  bins and three independent samples. Verified unchanged subtitles stay as-is.
  Uniform shifts up to 60 seconds may be staged and reverified. Wrong cuts,
  meaningful drift, sparse dialogue and uncertain results are not forced to fit.
- Missing/unverified sidecars: identify from read-only Bazarr metadata or NFO,
  download full English candidates, then verify before installation. No matches
  means UNRESOLVED, not fabricated success. Movie IDs and TV season/episode IDs
  are distinct. Ambiguous metadata or audio language requires review.
- No clear identification: single-file overrides `--imdb tt...`, and for TV
  `--season N --episode N`. An explicit `--audio-stream N` permits choosing the
  English dialogue stream when tags are missing. Untagged sidecars are not
  assumed English. Multiple English sidecars are reported rather than deleted.
- SRT, ASS/SSA, VTT can be analyzed. Styled sidecars needing correction are staged
  as SRT but not silently overwritten or stripped of styling.
- No downloaded subtitles are embedded, no tracks are deleted, no subtitle
  defaults changed, and no provider blacklisting occurs. Automated verification
  cannot establish 100% correctness. Embedding provider text remains disabled
  pending a separate explicit policy/implementation; it is not an undocumented
  side effect of `--apply`.

## State and rollback

Default state: `~/Library/Application Support/SubtitleMaintenance/`.
Contains `state.db`, `latest-report.json`, transcripts, downloaded candidates,
staging, backups and per-target transaction receipts. Do not delete backups to
clear a cache. Unknown/errors are retryable; successful verification caches are
fingerprinted by files, policy and mode. A single-process lock prevents duplicate
maintenance jobs. The ASR worker shares the existing Whisper GPU lock, processes
serially, limits its cache and exits after each transcription. Heavy audio/file
I/O can still affect playback; start small.

```sh
~/scripts/subtitle-maintain.py --restore "/path/to/receipt.json"
~/scripts/subtitle-maintain.py --restore "/path/to/receipt.json" --apply
```

Restore verifies both current and backup hashes and preserves the replaced file
in rollback-backups. Receipts for newly created files do not automatically delete
them. Recovery receipts marked PREPARED require inspection after an interruption.
OCR retains video timestamps, but that does not guarantee library watchers won't
notice changes. The tool never clears third-party queues.

## Configuration and provider access

Local configuration: `~/scripts/subtitle-maintenance.json`, containing paths and
policy only, not passwords. `python` points to the existing MLX virtualenv;
`model` to its local model. Old native-word caches can be reused through
`legacy_transcripts` when media fingerprints and chosen audio stream match.

The installed configuration uses the existing Bazarr container only as a
credential/runtime bridge. It does NOT invoke Bazarr's download/save/sync hooks.
For standalone operation, provide `OPENSUBTITLES_API_KEY`,
`OPENSUBTITLES_USERNAME`, and `OPENSUBTITLES_PASSWORD` through a secure environment
and remove `bazarr_container`. Do not paste secrets into commands or reports.
The configured Python needs requests and PyYAML in either mode used standalone.
Without the Bazarr database, NFO metadata or explicit IDs are needed. Bazarr can
be retired only after identification and credential provisioning are independent.

## Deliberately separate

Whisper subtitle generation, legacy restoration utilities, specialized audits,
forced-track removal, default-track editing and
redundant-OCR cleanup remain separate for now. They aren't silently chained into
the normal maintenance path. No claim is made that this initial release replaces
every older feature or that every file can be automatically verified. Automated
generation of missing subtitle text is not enabled by this tool.

Redundant-sidecar quarantine is now available as the separate explicit
`--cleanup-sidecars` mode. Human-approved hashes in configuration are protected.

## Validation

`python3 -m unittest discover -s tests -v` from the project folder.
Regression controls cover early display, wrong offsets/cuts, sparse matches,
sidecar classification, safe install/restore and preview isolation. The initial
read-only live pilot verified four repaired QI episodes and preserved the
human-approved S09E01 checksum. No library-wide apply has been run with this tool.

The installed provider preview also searched and downloaded three S12E01
candidates successfully, using an explicit audio-stream selection because that
file lacks an audio language tag. None passed all checks; its original sidecar
checksum was confirmed unchanged. OCR and movie-specific paths still need broader
real-media pilot coverage before unattended library-wide apply.
