# Subtitle Maintain

## Shared configuration and portable installation

See [README.md](README.md) for clone setup and shared settings. The loader now
finds `subtitle-maintenance.json` beside the scripts, supports `--config` and
`SUBTITLE_MAINTENANCE_CONFIG`, and resolves relative host paths against that file.
Media/show roots, standalone Whisper tools/service, Sonarr paths and Subarr
backup settings no longer require source edits. Private installed settings and
existing state locations are preserved. No verification gates were changed.


One entry point for conservative English subtitle maintenance. Preview is the
default. Preview may download candidates and transcribe audio into the state
folder, but never replaces media. `--scan-only` is an inexpensive inventory with
no speech recognition or provider downloads.

## Commands

### Verified drift correction (opt-in trial)

`--allow-drift-correction` can repair a consistent playback-speed mismatch in an
existing sidecar or provider candidate. It fits one global scale plus offset,
not scene-by-scene edits. Preview remains the default; add `--apply` only after
review. No video/audio streams are changed. Original sidecar backups and receipts
remain available on apply. Combined episode filenames such as S03E08-E09 are
excluded from drift fitting. Wrong cuts, sparse evidence and inconsistent drift
remain unresolved; automated verification is not a guarantee of correctness.

The trial requires at least 100 matched cues outside five held-out sample regions,
a scale within 0.95–1.05, an intercept within 60 seconds, and tight overall/regional
fit residuals. Corrected timestamps must then pass the unchanged full-dialogue
gates and five independently transcribed samples. The staged SRT is checked again
without further shifting or fitting. This may need two additional audio samples
per episode; existing downloads/full transcripts are reused where compatible.
The option is included in the policy cache key so earlier unresolved results can
be reconsidered. Reports retain fitted scale, offset and rejection evidence.

```sh
~/scripts/subtitle-maintain.py '/Volumes/Media/Plex/TV/Will & Grace' \
  --map-episode-titles \
  --assume-single-untagged-english \
  --allow-drift-correction \
  --max-downloads 500 --max-candidates 10
```

### Alternate episode numbering (opt-in)

`--map-episode-titles` searches BOTH library numbering and TVmaze's alternate numbering
when a unique normalized title matches within the IMDb-confirmed show. Episode
titles currently come from the read-only Bazarr library database; missing,
duplicate or unnumbered matches require review. No fuzzy or blanket season offsets
are used. Normalization ignores case/punctuation and treats `&` as `and`.
The full catalog is cached for seven days in `episode-catalogs/`; reports preserve
the local identity and TVmaze episode ID, URL and air date. Air date is provenance,
not a comparison gate (the current local database does not supply it).

This is an alternate search, not proof that OpenSubtitles uses TVmaze ordering.
Downloads must still pass all dialogue/timing gates. Library filenames, Sonarr
metadata and video files are not renumbered. This option only runs when a provider
search is needed; trusted embedded/verified local subtitles retain their usual path.
Do not enable it globally without testing each show's catalog alignment.

Results from both searches are deduplicated by provider file ID. Recognizable
matching titles in releases rank first; explicit other catalog episode titles
after SxxExx/xxXxx markers are excluded before the candidate limit. Untitled or
unrecognized releases remain eligible and require dialogue verification. Short
ambiguous title fragments are not negative evidence. This does not exhaust every
possible numbering scheme. Rejected titles and search provenance appear in JSON.
If one search fails, the other can still yield a verified replacement; otherwise
the result is `DEFERRED_PROVIDER_SEARCH`, not a claim that all searches succeeded.

Trial ranking also promotes short exact titles (e.g. CIAPOW) at token boundaries.
Long titles with minor spelling differences rank after exact matches and before
unknown releases. Approximate matches NEVER authorize installation or rejection.
The same dialogue/timing gates remain required.

Use `--max-candidates 10` to attempt up to ten unique provider files per episode
instead of the default three. This is separate from `--max-downloads`, the run-wide
new-download cap. `MORE_CANDIDATES_REMAIN` means eligible results were left beyond
the attempt limit. Reruns reuse cached downloads/transcripts but may repeat failed
verification before reaching additional candidates; there is no per-candidate
resume cursor. Start with a preview:

```sh
~/scripts/subtitle-maintain.py '/Volumes/Media/Plex/TV/American Dad!/Season 12' --map-episode-titles --max-candidates 10 --max-downloads 50
```

```sh
~/scripts/subtitle-maintain.py '/Volumes/Media/Plex/TV/American Dad!/Season 10' --limit 4 --map-episode-titles --max-downloads 12
```

### Missing audio language and download limits

Analysis audio selection prefers English dialogue tracks marked default. If more
than one qualifies (or none is default), it chooses fewer channels, then the lowest
stream index. Commentary/audio-description titles and dispositions are excluded.
This is an efficiency heuristic, not a promise to reproduce Plex's user-specific
playback selection. It changes no video tags/defaults. The selected analysis stream
prints before verification and is recorded in the JSON report. `--audio-stream`
remains a single-file explicit override.

`--assume-single-untagged-english` allows normal verification to use exactly one
audio stream whose language is absent or `und`, with no commentary/description
title. It does not edit the video or detect the spoken language. Multiple audio
untagged streams and explicit other languages still require review. Only enable it on
material you know is English.

`--max-downloads 50` overrides the local per-run cap (default 20). Cached subtitle
downloads do not count. Zero permits cached candidates only. Provider quota and
rate-limit cooldowns still apply independently. `DEFERRED_DOWNLOAD_BUDGET` means
some candidates were not tested, not that their dialogue verification failed.

`--tag-missing-audio-english` is a separate metadata-only mode for MKV, not subtitle
repair. It previews by default; `--apply` changes a verified copy with mkvpropedit,
retains a full original backup/receipt, installs it and preserves timestamps.
Use `--restore` with that receipt for rollback. Non-MKV files remain untouched:
the assumption option works for their verification without container conversion.
Media watchers can still notice metadata edits despite retained timestamps.

```sh
~/scripts/subtitle-maintain.py "/Volumes/Media/Plex/TV/QI" --limit 50 --assume-single-untagged-english --max-downloads 50
~/scripts/subtitle-maintain.py "/path/to/video.mkv" --tag-missing-audio-english
```

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
file. `WOULD_INSTALL` and `DOWNLOADED_VERIFIED` include the selected OpenSubtitles
release, provider file ID, source link, timing shift and destination. These are
also recorded as `selected_provider`, `selected_offset_seconds` and `destination`
in the JSON report. Positive shifts move subtitle cues later; zero is unchanged.
Progress also prints per
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
  Uniform shifts up to 60 seconds may be staged and reverified. Global drift
  correction requires the explicit trial option above. Wrong cuts, inconsistent
  drift, sparse dialogue and uncertain results are not forced to fit.
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

Helper transport failures now report a sanitized cause (timeout, EOF, malformed
response or OS exception class), action and available exit status. The failed
helper is closed/reaped before another request; a read-only search retries once.
Uncertain download requests are NOT replayed automatically, since a provider may
already have counted them. Repeated transport failures use a five-minute cooldown;
HTTP/quota/auth cooldowns retain their separate policy. Legacy generic transport
cooldowns are superseded on restart; new failures and provider limits are not.
No raw responses, stderr, signed links or credentials are captured in diagnostics.

The standalone helper refreshes its own in-memory login once when the download
API rejects a token with HTTP 401. It never writes tokens or configuration back
to Bazarr. Repeated authentication failures report an authentication error and
retry time; quota/rate-limit/server cooldowns remain enforced. Old cooldown records
specifically reporting download HTTP 401 `invalid token` are superseded by this
recovery policy; new repeated failures are not bypassed. Cached downloads remain
usable during cooldowns. Restart an already-running script to load the fix.
Both clients still share the OpenSubtitles account's provider-side limits; this
does not create a separate quota or modify Bazarr's own authentication handling.

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
