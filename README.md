# Subtitle maintenance

Conservative English subtitle maintenance, audits, and related media utilities.
Preview is the default for the main tool. See the [full workflow guide](subtitle-maintenance-README.md)
for verification behavior and apply/restore commands.

## Set up a clone

1. Clone or fork this repository into any folder.
2. Use Python 3.10 or newer. Install ffmpeg/ffprobe and MKVToolNix on your `PATH`.
   OCR additionally needs Subtitle Edit's `seconv`. Native MLX transcription
   requires a compatible Apple Silicon runtime and model; configuring paths
   does not make MLX cross-platform. Provider access needs `requests` and
   `PyYAML` in the configured Python, or the documented Bazarr bridge.
3. Copy `subtitle-maintenance.example.json` to `subtitle-maintenance.json`.
   Set `media_root`, `python`, and `model`. Configure only the integrations you use.
4. Run a read-only inventory:

```sh
python3 subtitle-maintain.py --scan-only
```

A normal preview (`python3 subtitle-maintain.py`) can download subtitle candidates
and transcribe into the state folder. Use `--scan-only` for the initial inventory.
The local config is ignored by Git. Never put credentials in the example or local
settings; use the existing environment/owner-only secrets mechanism described in
[SECURITY.md](SECURITY.md).

## Shared configuration

`script_config.py` is the standard-library-only loader. Config selection is:
`--config /path/settings.json`, then `SUBTITLE_MAINTENANCE_CONFIG`, then
`subtitle-maintenance.json` beside the scripts. An explicit missing or malformed
config is an error. Missing default config allows commands with explicit inputs.
Supported command-line settings override JSON. `~` is expanded in host paths;
relative host paths are relative to the config file. Prefix relative executable
or local model paths with `./`; bare executable names are looked up on `PATH`.
Container paths are kept exactly as supplied.

| Setting | Used by |
| --- | --- |
| `media_root` | Main tool's default input; Whisper default input; default base for `TV/QI` and `TV/EastEnders` |
| `state_dir` | Main tool's database, reports, backups and caches |
| `python`, `model` | Main tool's native transcription runtime/model |
| `tools` | Main tool subprocesses and Whisper: `ffmpeg`, `ffprobe`, `mkvmerge`, `mkvpropedit`, `curl`; backup: `docker`; OCR: `seconv` |
| `whisper.service_url`, `whisper.model`, `whisper.state_dir` | Standalone Whisper backfill |
| `shows.qi_root`, `shows.eastenders_root` | Optional explicit show folders; otherwise derived from `media_root` |
| `sonarr.url` | EastEnders and Plex-to-Sonarr importer; use a URL ending in `/api/v3` |
| `sonarr.eastenders_root` | EastEnders folder as seen inside Sonarr |
| `sonarr.host_root`, `sonarr.sonarr_root`, `sonarr.work_dir` | Plex-to-Sonarr importer defaults; existing command-line options take precedence |
| `backup` | Subarr container/user, container Python/database/directory, retention count |

Existing provider keys (`bazarr_database`, `bazarr_container`, `path_mappings`),
`legacy_transcripts`, policy limits, and approved hashes remain supported.
Keep `path_mappings` as explicit absolute host/container pairs. The main native
model is separate from `whisper.model`, which is a service model name.
`WHISPER_ASR_URL` overrides the Whisper JSON service URL; `--service-url` overrides
both. `SONARR_URL` overrides the importer's configured URL.

State defaults preserve `~/Library/Application Support/<tool>` on macOS and use
`$XDG_STATE_HOME/<tool>` or `~/.local/state/<tool>` elsewhere. Existing state is not
moved. `tools` overrides apply to the main workflow, Whisper and backup helper;
other standalone audit/metadata utilities continue to discover tools on `PATH`.
OCR also looks for `seconv-5.2.0/seconv` beside the repo if not configured/on PATH.

```sh
python3 subtitle-maintain.py /another/library --config /path/settings.json --scan-only
python3 whisper-subtitles.py --config /path/settings.json --path /another/library --dry-run
python3 fix_qi_english_subtitle_language.py --help
python3 plex-sonarr-import.py --help
python3 subarr-daily-backup.py --config /path/settings.json
```

The existing `subarr-daily-backup.sh` entry point forwards to the Python helper,
so scheduled callers can retain its filename. Backup paths in JSON are container
paths. The backup still checks SQLite integrity and skips an already verified
daily backup.

The legacy `eastenders-fix.py` reads the same config via the environment or default
location, but retains its existing live module-level behavior. Do not import it
or run it as a setup test. QI settings relocate its show boundary; they do not turn
its show-specific language assumptions into a general library policy.

## Tests

```sh
python3 -m unittest discover -s subtitle_maintenance/tests -v
```

Tests use temporary fixtures and mocked services. They do not process live media.
