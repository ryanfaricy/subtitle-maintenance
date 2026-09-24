# Subtitle maintenance

[![Checks](https://github.com/ryanfaricy/subtitle-maintenance/actions/workflows/checks.yml/badge.svg)](https://github.com/ryanfaricy/subtitle-maintenance/actions/workflows/checks.yml)

MIT licensed • Python 3.10+ • macOS/Linux • preview-first

Conservative English subtitle maintenance, audits, and related media utilities.
Preview is the default for the main tool. See the [full workflow guide](subtitle-maintenance-README.md)
for verification behavior and apply/restore commands.

## Set up a clone

1. Clone or fork this repository into any folder. The main CLI is supported;
   QI/EastEnders scripts are specialist utilities with show-specific assumptions.
2. Use Python 3.10 or newer. Install ffmpeg/ffprobe and MKVToolNix on your `PATH`.
   OCR additionally needs Subtitle Edit's `seconv`. Native MLX transcription
   requires a compatible Apple Silicon runtime and model; configuring paths
   does not make MLX cross-platform. Provider access needs `requests` and
   `PyYAML` in the configured Python, or the documented Bazarr bridge.
3. Copy `subtitle-maintenance.example.json` to `subtitle-maintenance.json`.
   Set `media_root`, `python`, and `model`. Configure only the integrations you use.
4. Install the command in a virtual environment and check setup:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
subtitle-maintain --doctor
```

Use `'.[providers]'` instead of `.` for standalone provider dependencies, or
`'.[mlx]'` for optional Apple Silicon transcription dependencies. The main CLI
and metadata logic use the Python standard library; external media tools are
installed separately. Linux supports the core/service-based paths; Windows is
not currently supported. `doctor` checks local availability, not real provider,
OCR or model interoperability.

5. Run a read-only inventory:

```sh
python3 subtitle-maintain.py --scan-only
```

A repair preview (`subtitle-maintain /path/to/media`) can download subtitle candidates
and transcribe into the state folder. Use `--scan-only` for the initial inventory.
The local config is ignored by Git. Never put credentials in the example or local
settings; use the existing environment/owner-only secrets mechanism described in
[SECURITY.md](SECURITY.md).

## Shared configuration

`script_config.py` is the standard-library-only loader. Config selection is:
`--config /path/settings.json`, then `SUBTITLE_MAINTENANCE_CONFIG`, then
`subtitle-maintenance.json` beside the scripts, then
`$XDG_CONFIG_HOME/subtitle-maintenance/config.json` (default `~/.config/subtitle-maintenance/config.json`).
For a wheel install, use `--config` or the user config location. An explicit missing or malformed
config is an error. Missing default config allows commands with explicit inputs.
Unknown keys, invalid types/ranges, and config-based mutation flags are rejected.
Use the optional `policy` section for limits; flat legacy keys still work but
duplicate definitions are an error. Supported command-line settings override JSON. `~` is expanded in host paths;
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
python3 subarr-daily-backup.py --config /path/settings.json --apply
```

The existing `subarr-daily-backup.sh` entry point forwards to the Python helper,
so scheduled callers can retain its filename. Backup paths in JSON are container
paths. The backup still checks SQLite integrity and skips an already verified
daily backup.

## Safe defaults and migration to 0.2

- Bare main, Whisper, EastEnders and backup commands show help without loading
  config, scanning media, reading credentials or making service calls.
- The main repair command previews unless `--apply` is explicit. A preview may
  download/transcribe to state; use `--scan-only` for an inventory without either.
- Whisper now previews by default, without writing state or contacting ASR.
  **Existing backfill schedules must add `--apply` to continue generation.**
- EastEnders now has an import-safe `main()` and previews by default. Imports and
  renames require `--apply`; redundant-source deletion additionally requires
  `--cleanup-redundant`. Preview never deletes a source, including cleanup cases.
- Subarr backup now requires `--apply` to create a backup. **Existing backup
  schedules must add `--apply`.** No scheduler entries are changed by installation.
- Config cannot enable apply, deletion or cleanup. Audit commands remain read-only.
- QI settings relocate its show boundary; they do not generalize its assumptions.

## Design and contributing

Start with the [architecture](docs/architecture.md), [failure/recovery walkthrough](docs/design-walkthrough.md),
and [synthetic example](examples/README.md). [CONTRIBUTING.md](CONTRIBUTING.md)
describes development setup, testing and review expectations. See [LICENSE](LICENSE)
for MIT terms. The project is an early release; unit tests are not a claim of
universal subtitle correctness or complete external-integration coverage.

## Tests

```sh
python3 -m unittest discover -s subtitle_maintenance/tests -v
```

Tests use temporary fixtures and mocked services. They do not process live media.
