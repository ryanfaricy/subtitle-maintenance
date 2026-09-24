import argparse
import fcntl
import hashlib
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

from script_config import load_config, state_path

from . import __version__, media, native
from .common import atomic_json, digest, fingerprint, install
from .providers import Provider
from .workflow import process


def selection_lines(data):
    """Display the verified winner, not merely the last searched candidate."""
    selected = data.get("selected_provider")
    if not selected:
        return []
    offset = data.get("selected_offset_seconds", 0)
    lines = [
        f"Selected: {selected['provider']} — {selected.get('release') or '(unnamed release)'} (file ID {selected['file_id']})"
    ]
    if selected.get("url"):
        lines.append("Source: " + selected["url"])
    lines.append(
        "Timing: " + (f"{offset:+.3f}s shift (positive = later)" if offset else "unchanged")
    )
    if data.get("selected_scale", 1) != 1:
        lines[-1] = f"Timing: scale {data['selected_scale']:.6f}, offset {offset:+.3f}s"
    lines.append("Destination: " + data["destination"])
    return lines


def build_parser():
    parser = argparse.ArgumentParser(
        description="Conservative English subtitle maintenance. Preview by default.",
        epilog="First run: subtitle-maintain init; check tools: subtitle-maintain doctor [--install]. Use ./init or ./doctor for folders with those names.",
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        help="JSON settings (default: beside the scripts; SUBTITLE_MAINTENANCE_CONFIG overrides)",
    )
    parser.add_argument("--state-dir", type=Path, help="Override config state_dir")
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Check local setup without processing media or contacting services",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="With --doctor only: propose and confirm core tool installation",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Inventory only: no provider downloads or speech transcription",
    )
    parser.add_argument(
        "--audit",
        choices=["coverage", "defaults"],
        help="Read-only metadata audit; no transcription/downloads",
    )
    parser.add_argument(
        "--cleanup-sidecars",
        action="store_true",
        help="Preview redundant English sidecar quarantine; requires --apply to move",
    )
    parser.add_argument(
        "--restore-quarantine",
        type=Path,
        help="Restore a quarantine receipt; preview unless --apply",
    )
    parser.add_argument(
        "--cache-only", action="store_true", help="Use only existing speech transcripts"
    )
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument(
        "--keep-existing-95",
        action="store_true",
        help="Keep unchanged SRTs with >=95%% word and timing agreement (1.5s tolerance); skip repair/provider search",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--rescan", action="store_true")
    parser.add_argument(
        "--audio-stream", type=int, help="Explicit ffprobe audio stream index; one video only"
    )
    parser.add_argument(
        "--assume-single-untagged-english",
        action="store_true",
        help="Treat one untagged audio stream as English without modifying the video",
    )
    parser.add_argument(
        "--tag-missing-audio-english",
        action="store_true",
        help="Separate MKV metadata-only mode; single untagged audio only; preview unless --apply",
    )
    parser.add_argument(
        "--max-downloads",
        type=int,
        help="Maximum new provider downloads this run (default config: 20); cached files do not count",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        help="Maximum unique candidates attempted per video (default config: 3)",
    )
    parser.add_argument(
        "--allow-drift-correction",
        action="store_true",
        help="Trial global timing scale fit with independent verification; preview unless --apply",
    )
    parser.add_argument(
        "--map-episode-titles",
        action="store_true",
        help="Opt-in TVmaze alternate numbering by unique episode title; library unchanged",
    )
    parser.add_argument("--imdb", help="Explicit tt... movie or series ID; one video only")
    parser.add_argument("--season", type=int)
    parser.add_argument("--episode", type=int)
    parser.add_argument(
        "--restore", type=Path, help="Restore a committed receipt, preview unless --apply"
    )
    return parser


def main(argv=None):
    parser = build_parser()
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        parser.print_help()
        return 0
    if arguments[0] == "init":
        from .setup import initialize

        return initialize(arguments[1:])
    if arguments[0] == "doctor":
        arguments = ["--doctor", *arguments[1:]]
    args = parser.parse_args(arguments)
    if args.install and not args.doctor:
        parser.error("--install requires doctor or --doctor")
    if args.limit < 0:
        parser.error("--limit must be zero or greater")
    if args.doctor and (
        args.paths
        or args.apply
        or args.scan_only
        or args.audit
        or args.cleanup_sidecars
        or args.restore
        or args.restore_quarantine
        or args.tag_missing_audio_english
    ):
        parser.error("--doctor cannot be combined with a processing mode or paths")
    if args.max_downloads is not None and args.max_downloads < 0:
        parser.error("--max-downloads must be zero or greater")
    if args.max_candidates is not None and args.max_candidates < 1:
        parser.error("--max-candidates must be at least 1")
    modes = [
        args.audit is not None,
        args.cleanup_sidecars,
        args.restore_quarantine is not None,
        args.restore is not None,
        args.scan_only,
        args.tag_missing_audio_english,
    ]
    if sum(modes) > 1:
        parser.error("Choose only one audit, cleanup, restore, or scan mode")
    if args.audit and args.apply:
        parser.error("--audit is read-only")
    if args.restore_quarantine and args.paths:
        parser.error("--restore-quarantine cannot be combined with paths")
    if args.restore and (args.paths or args.scan_only):
        parser.error("--restore cannot be combined with paths or --scan-only")
    if args.scan_only and args.apply:
        parser.error("--scan-only cannot be combined with --apply")
    try:
        config = load_config(args.config)
    except ValueError as e:
        parser.error(str(e))
    if args.doctor:
        from .doctor import diagnose

        if args.install:
            from .dependencies import install_missing

            return install_missing(config)
        return diagnose(config)
    from .common import configure_tools

    configure_tools(config)
    args.state_dir = args.state_dir or state_path(config)
    if (
        not args.paths
        and not (args.restore or args.restore_quarantine)
        and config.get("media_root")
    ):
        args.paths = [Path(config["media_root"])]
    config.setdefault("python", sys.executable)
    config.setdefault("model", "")
    config["cache_only"] = args.cache_only
    from .subtitles import DIALOGUE_SCORING_VERSION

    config["dialogue_scoring_version"] = DIALOGUE_SCORING_VERSION
    config["assume_single_untagged_english"] = args.assume_single_untagged_english
    config["map_episode_titles"] = args.map_episode_titles
    config["allow_drift_correction"] = args.allow_drift_correction
    config["keep_existing_95"] = args.keep_existing_95
    if args.keep_existing_95:
        from .preservation import POLICY_VERSION

        config["preservation_policy_version"] = POLICY_VERSION
    if args.max_downloads is not None:
        config["max_downloads"] = args.max_downloads
    if args.max_candidates is not None:
        config["max_candidates"] = args.max_candidates
    args.state_dir = args.state_dir.expanduser().resolve()
    args.identity = None
    if args.imdb:
        import re

        if not re.fullmatch(r"tt\d+", args.imdb):
            parser.error("--imdb must be an IMDb tt identifier")
        args.identity = dict(imdb=args.imdb, source="explicit user input")
        if (args.season is None) != (args.episode is None):
            parser.error("Supply both --season and --episode")
        if args.season is not None:
            args.identity.update(season=args.season, episode=args.episode)
    if args.audio_stream is not None or args.imdb:
        if len(args.paths) != 1 or not args.paths[0].expanduser().is_file():
            parser.error("Metadata/audio overrides require one video file")
    if not (args.paths or args.restore or args.restore_quarantine):
        parser.error("Provide a video or folder, or configure media_root")
    for path in args.paths:
        if not path.expanduser().exists():
            parser.error("Input path does not exist: " + str(path))
    args.state_dir.mkdir(parents=True, exist_ok=True)
    with (args.state_dir / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another subtitle-maintain job is already running")
        if args.restore_quarantine:
            from .housekeeping import restore

            print(
                ("Restored: " if args.apply else "Would restore: ")
                + restore(args.restore_quarantine, args.apply)
            )
            return 0
        if args.restore:
            r = json.loads(args.restore.read_text())
            target = Path(r["target"])
            backup = Path(r["backup"]) if r.get("backup") else None
            if not backup:
                parser.error("This receipt created a new file; automatic deletion is not supported")
            if (
                r["status"] != "COMMITTED"
                or digest(backup) != r["original_sha256"]
                or digest(target) != r["installed_sha256"]
            ):
                parser.error("Restore checksum/state mismatch")
            print("Restore:", target)
            if args.apply:
                install(
                    backup,
                    target,
                    args.state_dir / "rollback-backups",
                    r["installed_sha256"],
                    target,
                    fingerprint(target),
                )
            return 0
        if not args.paths:
            parser.error("Provide a video or folder")
        db = sqlite3.connect(args.state_dir / "state.db")
        db.execute(
            "create table if not exists results (video text primary key, signature text, result text, updated real)"
        )
        provider = Provider(config, args.state_dir)
        counts = Counter()
        report = []
        print(
            "Scanning; "
            + ("APPLY" if args.apply else "PREVIEW")
            + " mode. State: "
            + str(args.state_dir),
            flush=True,
        )
        try:
            for n, video in enumerate(media.videos(args.paths, args.state_dir), 1):
                if args.limit and n > args.limit:
                    break
                print(f"[{n}] {video}", flush=True)
                transcription_failed = False
                try:
                    sidecars, unknown = media.sidecars(video)
                    signature = hashlib.sha256(
                        json.dumps(
                            [
                                __version__,
                                config,
                                args.apply,
                                args.scan_only,
                                args.no_download,
                                args.audio_stream,
                                args.identity,
                                fingerprint(video),
                                [(str(s), digest(s)) for s in sidecars + unknown],
                            ],
                            sort_keys=True,
                        ).encode()
                    ).hexdigest()
                    old = db.execute(
                        "select result from results where video=? and signature=?",
                        (str(video), signature),
                    ).fetchone()
                    data = json.loads(old[0]) if old else None
                    terminal = {"TRUSTED_EMBEDDED", "VERIFIED", "HUMAN_APPROVED", "KEPT_EXISTING"}
                    if args.tag_missing_audio_english:
                        from .audio_tags import tag_missing

                        data = tag_missing(video, args.apply, args.state_dir, config)
                    elif args.audit or args.cleanup_sidecars:
                        from .housekeeping import inspect

                        data = inspect(
                            video, args.audit or "cleanup", args.apply, args.state_dir, config
                        )
                    elif data and data["status"] in terminal and not args.rescan:
                        data = dict(data, cached=True)
                    else:
                        data = process(video, args, config, args.state_dir, provider)
                except native.TranscriptionError as e:
                    data = dict(video=str(video), status="ERROR", error=str(e))
                    signature = ""
                    transcription_failed = True
                except Exception as e:
                    data = dict(video=str(video), status="ERROR", error=str(e))
                    signature = ""
                counts[data["status"]] += 1
                report.append(data)
                print(
                    "  "
                    + data["status"]
                    + (": " + data.get("error", "") if data.get("error") else ""),
                    flush=True,
                )
                if data.get("detail"):
                    print("    " + data["detail"], flush=True)
                for line in selection_lines(data):
                    print("    " + line, flush=True)
                for item in data.get("cleanup_candidates", []):
                    print("    " + item, flush=True)
                db.execute(
                    "insert or replace into results values (?,?,?,?)",
                    (str(video), signature, json.dumps(data), time.time()),
                )
                db.commit()
                atomic_json(
                    args.state_dir / "latest-report.json",
                    dict(version=__version__, counts=dict(counts), results=report),
                )
                if transcription_failed:
                    print(
                        "Stopping: restore transcription before retrying; progress is saved.",
                        flush=True,
                    )
                    break
        finally:
            provider.close()
            db.close()
    print("Summary:", dict(counts), flush=True)
    return 1 if counts["ERROR"] else 0
