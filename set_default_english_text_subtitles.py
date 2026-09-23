#!/usr/bin/env python3
"""Set a non-forced English text subtitle default only when none exists.

Candidates come from audit_default_english_subtitles.py's CSV. Preview is the
default. Apply mode changes Matroska metadata only, restores timestamps, and
verifies every edited file. Non-English defaults are never changed, and tracks
tagged und are never assumed to be English.
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


TEXT_CODEC_MARKERS = (
    "subrip", "srt", "substationalpha", "ass", "ssa", "webvtt", "utf-8",
)
ENGLISH_TAGS = {"en", "eng", "english", "en-us", "en-gb"}
CANDIDATE_STATUSES = {"ENGLISH_TEXT_NOT_DEFAULT", "AMBIGUOUS_ENGLISH_DEFAULTS"}


def identify(video: Path) -> list[dict]:
    result = subprocess.run(
        ["mkvmerge", "-J", str(video)],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout).get("tracks", [])


def props(track: dict) -> dict:
    return track.get("properties") or {}


def language(track: dict) -> str:
    value = props(track).get("language_ietf") or props(track).get("language") or "und"
    return str(value).strip().lower().replace("_", "-")


def is_english(track: dict) -> bool:
    value = language(track)
    return value in ENGLISH_TAGS or value.startswith("en-")


def is_text(track: dict) -> bool:
    codec = str(track.get("codec", "")).lower()
    return track.get("type") == "subtitles" and any(x in codec for x in TEXT_CODEC_MARKERS)


def is_forced(track: dict) -> bool:
    p = props(track)
    title = str(p.get("track_name") or "").lower().replace("_", " ").replace("-", " ")
    return bool(p.get("forced_track")) or "forced" in title.split()


def is_sdh(track: dict) -> bool:
    p = props(track)
    title = str(p.get("track_name") or "").lower().replace("_", " ").replace("-", " ")
    words = set(title.split())
    return bool(p.get("hearing_impaired_flag")) or bool(words & {"sdh", "cc", "hi"})


def is_default(track: dict) -> bool:
    return bool(props(track).get("default_track"))


def describe(track: dict) -> str:
    flags = []
    if is_default(track):
        flags.append("default")
    if is_forced(track):
        flags.append("forced")
    if is_sdh(track):
        flags.append("sdh")
    return (
        f"id={track.get('id')} uid={props(track).get('uid')} "
        f"codec={track.get('codec')} language={language(track)} "
        f"flags={','.join(flags) if flags else 'none'} "
        f"title={props(track).get('track_name') or 'untitled'}"
    )


def choose_track(tracks: list[dict]) -> dict | None:
    eligible = [
        track for track in tracks
        if is_text(track) and is_english(track) and not is_forced(track)
    ]
    if not eligible:
        return None
    def rank(track: dict) -> tuple[int, int, int, int, int]:
        title = str(props(track).get("track_name") or "").lower()
        title_words = set(title.replace("_", " ").replace("-", " ").split())
        full_hint = bool(title_words & {"full", "complete", "dialogue", "dialog"})
        limited_hint = bool(title_words & {
            "commentary", "comments", "signs", "songs", "lyrics", "foreign",
            "partial", "sample",
        })
        ocr_generated = "ocr text from bitmap" in title
        # Explicit full-dialogue labeling wins. Avoid limited/commentary tracks,
        # then prefer regular subtitles over SDH and native text over OCR.
        # Container order breaks remaining ties.
        return (
            0 if full_hint else 1,
            1 if limited_hint else 0,
            1 if is_sdh(track) else 0,
            1 if ocr_generated else 0,
            int(track.get("id", 10**9)),
        )

    return sorted(eligible, key=rank)[0]


def uid(track: dict) -> int | None:
    return props(track).get("uid")


def candidate_paths(report: Path, match: str) -> list[Path]:
    output = []
    with report.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") not in CANDIDATE_STATUSES:
                continue
            video = Path(row.get("video", "")).expanduser()
            if video.suffix.lower() != ".mkv":
                continue
            if match and match.lower() not in str(video).lower():
                continue
            output.append(video)
    return sorted(set(output))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Make the best explicitly English, non-forced text subtitle the "
            "sole English default while preserving non-English defaults."
        )
    )
    parser.add_argument("report", type=Path, help="Default-English audit CSV")
    parser.add_argument("--apply", action="store_true", help="Apply changes; default is preview")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N candidates")
    parser.add_argument("--match", default="", help="Only paths containing this text")
    parser.add_argument("--result", type=Path, help="Optional CSV result/manifest")
    args = parser.parse_args()

    report = args.report.expanduser().resolve()
    if not report.is_file():
        parser.error(f"Audit CSV not found: {report}")
    for tool in ("mkvmerge", "mkvpropedit"):
        if not shutil.which(tool):
            parser.error(f"{tool} is required")
    if args.limit < 0:
        parser.error("--limit cannot be negative")

    paths = candidate_paths(report, args.match)
    if args.limit:
        paths = paths[:args.limit]
    rows: list[dict[str, str]] = []
    proposed = changed = already_ok = skipped = errors = 0
    print(f"Candidates from audit: {len(paths)}", flush=True)

    for number, video in enumerate(paths, 1):
        print(f"Progress: {number}/{len(paths)}: {video}", flush=True)
        if not video.is_file():
            errors += 1
            print("  ERROR: file not found", flush=True)
            rows.append({"video": str(video), "action": "ERROR", "selected_track": "", "details": "file not found"})
            continue
        try:
            tracks = identify(video)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as error:
            errors += 1
            print(f"  ERROR: cannot identify: {error}", flush=True)
            rows.append({"video": str(video), "action": "ERROR", "selected_track": "", "details": str(error)})
            continue

        subtitle_tracks = [track for track in tracks if track.get("type") == "subtitles"]
        current_defaults = [track for track in subtitle_tracks if is_default(track)]
        english_defaults = [track for track in current_defaults if is_english(track)]
        selected = choose_track(tracks)
        if selected is None or uid(selected) is None:
            skipped += 1
            print("  SKIP: no explicitly English, non-forced text track with UID", flush=True)
            rows.append({"video": str(video), "action": "SKIP", "selected_track": "", "details": "no eligible track"})
            continue

        competing_english_defaults = [
            track for track in english_defaults if uid(track) != uid(selected)
        ]
        if is_default(selected) and not competing_english_defaults:
            already_ok += 1
            print(f"  ALREADY OK: {describe(selected)}", flush=True)
            rows.append({"video": str(video), "action": "ALREADY_OK", "selected_track": describe(selected), "details": ""})
            continue

        proposed += 1
        action = "CHANGE" if args.apply else "WOULD_CHANGE"
        print(f"  {action}: {describe(selected)}", flush=True)
        for track in competing_english_defaults:
            print(f"    clear competing English default: {describe(track)}", flush=True)
        for track in current_defaults:
            if is_english(track):
                continue
            print(f"    preserve non-English default: {describe(track)}", flush=True)

        if args.apply:
            original_stat = video.stat()
            command = ["mkvpropedit", str(video)]
            if not is_default(selected):
                command.extend([
                    "--edit", f"track:={uid(selected)}", "--set", "flag-default=1",
                ])
            for track in competing_english_defaults:
                command.extend([
                    "--edit", f"track:={uid(track)}", "--set", "flag-default=0",
                ])
            result = subprocess.run(command, text=True, capture_output=True)
            os.utime(video, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            if result.returncode:
                errors += 1
                detail = result.stderr.strip() or result.stdout.strip()
                print(f"  ERROR editing: {detail}", flush=True)
                rows.append({"video": str(video), "action": "ERROR", "selected_track": describe(selected), "details": detail})
                continue
            try:
                verified = identify(video)
            except (subprocess.CalledProcessError, json.JSONDecodeError) as error:
                errors += 1
                print(f"  ERROR verifying: {error}", flush=True)
                rows.append({"video": str(video), "action": "ERROR", "selected_track": describe(selected), "details": f"verify: {error}"})
                continue
            selected_uid = uid(selected)
            defaults = [track for track in verified if track.get("type") == "subtitles" and is_default(track)]
            verified_selected = next((track for track in defaults if uid(track) == selected_uid), None)
            verified_english_defaults = [track for track in defaults if is_english(track)]
            foreign_default_uids = {uid(track) for track in current_defaults if not is_english(track)}
            after_default_uids = {uid(track) for track in defaults}
            if (
                verified_selected is None
                or len(verified_english_defaults) != 1
                or not is_text(verified_selected) or not is_english(verified_selected)
                or is_forced(verified_selected)
                or not foreign_default_uids.issubset(after_default_uids)
            ):
                errors += 1
                print("  ERROR: post-edit verification failed", flush=True)
                rows.append({"video": str(video), "action": "ERROR", "selected_track": describe(selected), "details": "verification failed"})
                continue
            changed += 1
            print("    VERIFIED sole English text default; non-English defaults preserved", flush=True)
            rows.append({"video": str(video), "action": "CHANGED", "selected_track": describe(verified_selected), "details": "verified"})
        else:
            rows.append({"video": str(video), "action": "WOULD_CHANGE", "selected_track": describe(selected), "details": ""})

    if args.result:
        result_path = args.result.expanduser().resolve()
        result_path.parent.mkdir(parents=True, exist_ok=True)
        with result_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["video", "action", "selected_track", "details"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"Result CSV: {result_path}")

    print("Summary")
    print(f"  Candidates inspected: {len(paths)}")
    print(f"  {'Changed' if args.apply else 'Would change'}: {changed if args.apply else proposed}")
    print(f"  Already correct now: {already_ok}")
    print(f"  Skipped: {skipped}")
    print(f"  Errors: {errors}")
    if not args.apply:
        print("  Preview only; no files were changed.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
