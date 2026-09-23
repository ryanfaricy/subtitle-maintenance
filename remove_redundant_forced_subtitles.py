#!/usr/bin/env python3
"""Safely remove audited redundant forced subtitle tracks from Matroska files.

Default mode is preview-only. Apply mode revalidates every input, remuxes to a
temporary sibling, verifies the resulting track inventory, preserves timestamps,
and only then atomically replaces the original.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path


FIELDS = ["video", "action", "forced_track", "full_track", "details"]


class NotNeededError(ValueError):
    """The file is valid, but Any mode already reaches a full English track first."""


def identify(path: Path, mkvmerge: str) -> dict:
    result = subprocess.run(
        [mkvmerge, "-J", str(path)], text=True, capture_output=True,
        check=True, timeout=120,
    )
    return json.loads(result.stdout)


def prop(track: dict, name: str, default=None):
    return (track.get("properties") or {}).get(name, default)


def is_english(track: dict) -> bool:
    values = {
        str(prop(track, "language", "")).lower(),
        str(prop(track, "language_ietf", "")).lower(),
    }
    return bool(values & {"en", "eng"}) or any(value.startswith("en-") for value in values)


def is_forced(track: dict) -> bool:
    title = str(prop(track, "track_name", "")).lower()
    return bool(prop(track, "forced_track", False)) or "forced" in title


def is_text_subtitle(track: dict) -> bool:
    codec = f"{track.get('codec', '')} {prop(track, 'codec_id', '')}".lower()
    return not any(marker in codec for marker in ("pgs", "vobsub", "dvbsub", "dvd subtitle"))


def signature(track: dict) -> tuple:
    return (
        track.get("type"), track.get("codec"), prop(track, "language", "und"),
        prop(track, "language_ietf", ""), prop(track, "track_name", ""),
        bool(prop(track, "default_track", False)),
        bool(prop(track, "forced_track", False)),
        bool(prop(track, "hearing_impaired", False)),
    )


def validate_candidate(path: Path, forced_id: int, full_id: int, payload: dict) -> tuple[dict, dict]:
    tracks = payload.get("tracks", [])
    by_id = {int(track["id"]): track for track in tracks}
    if forced_id not in by_id:
        raise ValueError(f"forced track {forced_id} no longer exists")
    if full_id not in by_id:
        raise ValueError(f"full track {full_id} no longer exists")
    forced = by_id[forced_id]
    full = by_id[full_id]
    if forced.get("type") != "subtitles" or full.get("type") != "subtitles":
        raise ValueError("audited track IDs are no longer subtitle tracks")
    if not is_english(forced) or not is_english(full):
        raise ValueError("audited tracks are no longer both tagged English")
    if not is_forced(forced):
        raise ValueError("removal target is no longer marked/named forced")
    if is_forced(full):
        raise ValueError("retained full-dialogue target is marked/named forced")
    if forced_id >= full_id:
        raise ValueError("forced track no longer precedes the full track")
    earlier_full = [
        track for track in tracks
        if int(track["id"]) < forced_id
        and track.get("type") == "subtitles"
        and is_english(track)
        and not is_forced(track)
        and is_text_subtitle(track)
    ]
    if earlier_full:
        ids = ", ".join(str(track["id"]) for track in earlier_full)
        raise NotNeededError(
            f"English non-forced text track {ids} already precedes forced track {forced_id}"
        )
    return forced, full


def verify_output(before: dict, after: dict, removed_id: int) -> None:
    expected = Counter(
        signature(track) for track in before.get("tracks", [])
        if int(track["id"]) != removed_id
    )
    actual = Counter(signature(track) for track in after.get("tracks", []))
    if actual != expected:
        raise ValueError("output track inventory differs beyond the intended removal")
    before_duration = (before.get("container") or {}).get("properties", {}).get("duration")
    after_duration = (after.get("container") or {}).get("properties", {}).get("duration")
    if before_duration and after_duration:
        difference = abs(int(before_duration) - int(after_duration))
        if difference > 1_000_000_000:
            raise ValueError(f"output duration changed by {difference / 1e9:.3f} seconds")


def load_plan(report: Path) -> tuple[list[dict], Counter[str]]:
    selected: list[dict] = []
    skipped: Counter[str] = Counter()
    with report.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") != "SAFE_REDUNDANT_FORCED":
                skipped["not_safe"] += 1
                continue
            if row.get("forced_source") != "embedded" or row.get("full_source") != "embedded":
                skipped["not_both_embedded"] += 1
                continue
            try:
                forced_id = int(row["forced_track"])
                full_id = int(row["full_track"])
            except (KeyError, TypeError, ValueError):
                skipped["invalid_track_id"] += 1
                continue
            if forced_id >= full_id:
                skipped["full_already_first"] += 1
                continue
            row["forced_id"] = forced_id
            row["full_id"] = full_id
            selected.append(row)
    return selected, skipped


def remux(path: Path, forced_id: int, before: dict, mkvmerge: str) -> None:
    subtitle_ids = [
        int(track["id"]) for track in before.get("tracks", [])
        if track.get("type") == "subtitles" and int(track["id"]) != forced_id
    ]
    stat = path.stat()
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}.remove-forced-", suffix=".mkv", dir=path.parent,
    )
    os.close(temp_fd)
    temp = Path(temp_name)
    temp.unlink()
    try:
        command = [mkvmerge, "-o", str(temp)]
        if subtitle_ids:
            command += ["--subtitle-tracks", ",".join(map(str, subtitle_ids))]
        else:
            command += ["--no-subtitles"]
        command.append(str(path))
        subprocess.run(command, check=True, timeout=12 * 60 * 60)
        after = identify(temp, mkvmerge)
        verify_output(before, after, forced_id)
        os.utime(temp, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview or apply removal of high-confidence redundant forced MKV subtitle tracks."
    )
    parser.add_argument("--audit", type=Path, required=True, help="Audit CSV produced by audit_redundant_forced_subtitles.py")
    parser.add_argument("--report", type=Path, required=True, help="Write the removal preview/result CSV here")
    parser.add_argument("--apply", action="store_true", help="Actually remux and replace verified files")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N eligible files")
    parser.add_argument("--mkvmerge", default="mkvmerge", help="mkvmerge executable (default: mkvmerge)")
    args = parser.parse_args()

    audit = args.audit.expanduser().resolve()
    report = args.report.expanduser().resolve()
    if not audit.is_file():
        parser.error(f"Audit CSV does not exist: {audit}")
    mkvmerge = shutil.which(args.mkvmerge)
    if not mkvmerge:
        parser.error(f"mkvmerge not found: {args.mkvmerge}")
    if args.limit < 0:
        parser.error("--limit cannot be negative")

    plan, skipped = load_plan(audit)
    if args.limit:
        plan = plan[:args.limit]
    report.parent.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    audit_mtime = audit.stat().st_mtime_ns

    print(f"Mode: {'APPLY' if args.apply else 'PREVIEW (read-only)'}")
    print(f"Eligible high-confidence forced-before-full rows: {len(plan)}")
    print(f"Excluded probable/review rows: {skipped['not_safe']}")
    print(f"Excluded rows where full is already first: {skipped['full_already_first']}")

    with report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for number, item in enumerate(plan, 1):
            path = Path(item["video"])
            forced_id = int(item["forced_id"])
            full_id = int(item["full_id"])
            try:
                if not path.is_file():
                    raise ValueError("video no longer exists")
                if path.suffix.lower() != ".mkv":
                    raise ValueError("safe removal currently supports MKV files only")
                if path.stat().st_mtime_ns > audit_mtime:
                    raise ValueError("video changed after the audit; regenerate the audit")
                before = identify(path, mkvmerge)
                forced, full = validate_candidate(path, forced_id, full_id, before)
                details = (
                    f"remove track {forced_id} ({prop(forced, 'track_name', '') or forced.get('codec')}); "
                    f"retain track {full_id} ({prop(full, 'track_name', '') or full.get('codec')})"
                )
                if args.apply:
                    print(f"[{number}/{len(plan)}] REMUXING: {path}", flush=True)
                    remux(path, forced_id, before, mkvmerge)
                    action = "VERIFIED_AND_REPLACED"
                else:
                    print(f"[{number}/{len(plan)}] WOULD REMOVE: {path} :: {details}", flush=True)
                    action = "WOULD_REMOVE"
                counts[action] += 1
                writer.writerow({
                    "video": str(path), "action": action,
                    "forced_track": forced_id, "full_track": full_id,
                    "details": details,
                })
            except NotNeededError as error:
                action = "SKIPPED_NOT_NEEDED"
                counts[action] += 1
                print(f"[{number}/{len(plan)}] NOT NEEDED: {path} :: {error}", flush=True)
                writer.writerow({
                    "video": str(path), "action": action,
                    "forced_track": forced_id, "full_track": full_id,
                    "details": str(error),
                })
            except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
                action = "SKIPPED_VALIDATION_ERROR"
                counts[action] += 1
                detail = str(error)
                if isinstance(error, subprocess.CalledProcessError) and error.stderr:
                    detail = error.stderr.strip().splitlines()[-1]
                print(f"[{number}/{len(plan)}] SKIP: {path} :: {detail}", flush=True)
                writer.writerow({
                    "video": str(path), "action": action,
                    "forced_track": forced_id, "full_track": full_id,
                    "details": detail,
                })

    print("Summary")
    for action in (
        "WOULD_REMOVE", "VERIFIED_AND_REPLACED", "SKIPPED_NOT_NEEDED",
        "SKIPPED_VALIDATION_ERROR",
    ):
        print(f"  {action}: {counts[action]}")
    print(f"  Report: {report}")
    if not args.apply:
        print("  Preview only; no media or subtitle files were changed.")
    return 1 if counts["SKIPPED_VALIDATION_ERROR"] else 0


if __name__ == "__main__":
    sys.exit(main())
