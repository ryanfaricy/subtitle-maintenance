#!/usr/bin/env python3
"""Retag verified QI English text subtitle tracks from und to eng.

The candidate list comes from the missing-English-text audit CSV. Preview is
the default. Apply mode edits only Matroska track metadata, verifies the result,
and restores the media file's original timestamps.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


QI_ROOT = Path("/Volumes/Media/Plex/TV/QI").resolve()
TEXT_CODEC_MARKERS = ("subrip", "srt", "substationalpha", "ass", "ssa", "webvtt")


def identify(video: Path) -> list[dict]:
    result = subprocess.run(
        ["mkvmerge", "-J", str(video)],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout).get("tracks", [])


def is_text_track(track: dict) -> bool:
    codec = str(track.get("codec", "")).lower()
    return track.get("type") == "subtitles" and any(x in codec for x in TEXT_CODEC_MARKERS)


def language(track: dict) -> str:
    props = track.get("properties") or {}
    return str(props.get("language_ietf") or props.get("language") or "und").lower()


def is_forced(track: dict) -> bool:
    return bool((track.get("properties") or {}).get("forced_track"))


def audit_candidates(report: Path) -> list[Path]:
    candidates: list[Path] = []
    with report.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            video = Path(row.get("video", "")).expanduser()
            summary = row.get("embedded_subtitle_streams", "").lower()
            try:
                video.resolve().relative_to(QI_ROOT)
            except (ValueError, OSError):
                continue
            if video.suffix.lower() != ".mkv":
                continue
            if ":und" not in summary or not any(codec in summary for codec in ("subrip", "ass", "ssa")):
                continue
            candidates.append(video)
    return sorted(set(candidates))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview or fix verified English QI text tracks tagged as und."
    )
    parser.add_argument(
        "--report", type=Path,
        default=Path.home() / "missing-english-text-subtitles.csv",
        help="Audit CSV (default: ~/missing-english-text-subtitles.csv)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply language metadata changes. Without this flag, preview only.",
    )
    args = parser.parse_args()

    report = args.report.expanduser().resolve()
    if not report.is_file():
        parser.error(f"Audit report not found: {report}")
    for tool in ("mkvmerge", "mkvpropedit"):
        if not shutil.which(tool):
            parser.error(f"{tool} is required")

    candidates = audit_candidates(report)
    changed = already_fixed = no_target = errors = 0
    print(f"Audit candidates: {len(candidates)}", flush=True)

    for video in candidates:
        if not video.is_file():
            errors += 1
            print(f"ERROR missing file: {video}", flush=True)
            continue
        try:
            tracks = identify(video)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as error:
            errors += 1
            print(f"ERROR identifying {video}: {error}", flush=True)
            continue

        english = [
            track for track in tracks
            if is_text_track(track) and not is_forced(track)
            and language(track) in {"en", "eng", "en-us", "en-gb"}
        ]
        if english:
            already_fixed += 1
            print(f"ALREADY ENGLISH: {video}", flush=True)
            continue

        targets = [
            track for track in tracks
            if is_text_track(track) and not is_forced(track) and language(track) == "und"
        ]
        if not targets:
            no_target += 1
            print(f"SKIP no eligible und text track: {video}", flush=True)
            continue

        for track in targets:
            props = track.get("properties") or {}
            print(f"{'CHANGE' if args.apply else 'WOULD CHANGE'}: {video}", flush=True)
            print(
                f"  track id={track.get('id')} uid={props.get('uid')} "
                f"codec={track.get('codec')} language=und -> eng/en",
                flush=True,
            )

        if not args.apply:
            changed += 1
            continue

        original_stat = video.stat()
        command = ["mkvpropedit", str(video)]
        for track in targets:
            uid = (track.get("properties") or {}).get("uid")
            if uid is None:
                errors += 1
                print(f"ERROR missing track UID: {video}", flush=True)
                command = []
                break
            command.extend([
                "--edit", f"track:={uid}",
                "--set", "language=eng",
                "--set", "language-ietf=en",
            ])
        if not command:
            continue

        result = subprocess.run(command, text=True, capture_output=True)
        os.utime(video, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        if result.returncode:
            errors += 1
            print(f"ERROR editing {video}: {result.stderr.strip() or result.stdout.strip()}", flush=True)
            continue

        try:
            verified = identify(video)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as error:
            errors += 1
            print(f"ERROR verifying {video}: {error}", flush=True)
            continue
        target_uids = {(track.get("properties") or {}).get("uid") for track in targets}
        verified_ok = all(
            language(track) in {"en", "eng"}
            for track in verified
            if (track.get("properties") or {}).get("uid") in target_uids
        ) and target_uids.issubset({
            (track.get("properties") or {}).get("uid") for track in verified
        })
        if not verified_ok:
            errors += 1
            print(f"ERROR verification failed: {video}", flush=True)
            continue
        changed += 1
        print("  VERIFIED language metadata", flush=True)

    print("Summary")
    print(f"  Audit candidates: {len(candidates)}")
    print(f"  {'Files changed' if args.apply else 'Files that would change'}: {changed}")
    print(f"  Already correctly tagged: {already_fixed}")
    print(f"  No eligible track: {no_target}")
    print(f"  Errors: {errors}")
    if not args.apply:
        print("  Preview only; no files were changed.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
