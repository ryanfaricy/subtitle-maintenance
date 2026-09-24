#!/usr/bin/env python3
"""Audit videos for a default, non-forced English text subtitle stream.

The script only reads media metadata through ffprobe. It never changes media,
subtitle files, application databases, or library state.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

VIDEO_EXTENSIONS = {
    ".3gp",
    ".asf",
    ".avi",
    ".divx",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".mts",
    ".ogm",
    ".ogv",
    ".rm",
    ".rmvb",
    ".ts",
    ".vob",
    ".webm",
    ".wmv",
}
TEXT_CODECS = {
    "ass",
    "dvb_teletext",
    "eia_608",
    "eia_708",
    "hdmv_text_subtitle",
    "microdvd",
    "mov_text",
    "mpl2",
    "realtext",
    "sami",
    "srt",
    "ssa",
    "subrip",
    "text",
    "ttml",
    "webvtt",
}
ENGLISH_TAGS = {"en", "eng", "english"}
FIELDNAMES = [
    "video",
    "status",
    "english_subtitle_streams",
    "all_subtitle_streams",
    "error",
]


def stream_tag(stream: dict, name: str, default: str = "") -> str:
    for key, value in (stream.get("tags") or {}).items():
        if str(key).lower() == name.lower():
            return str(value)
    return default


def title_words(value: str) -> set[str]:
    return {
        word.strip("[](){}._-").lower()
        for word in value.replace("-", " ").replace("_", " ").split()
        if word
    }


def is_english(stream: dict) -> bool:
    language = stream_tag(stream, "language").strip().lower().replace("_", "-")
    if language in ENGLISH_TAGS or language.startswith("en-"):
        return True
    return bool(title_words(stream_tag(stream, "title")) & ENGLISH_TAGS)


def disposition(stream: dict, name: str) -> bool:
    return bool((stream.get("disposition") or {}).get(name))


def is_forced(stream: dict) -> bool:
    return disposition(stream, "forced") or "forced" in title_words(stream_tag(stream, "title"))


def is_text(stream: dict) -> bool:
    return str(stream.get("codec_name", "")).lower() in TEXT_CODECS


def probe(path: Path) -> list[dict]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "s",
            "-show_entries",
            "stream=index,codec_name:stream_tags=language,title:"
            "stream_disposition=default,forced,hearing_impaired",
            "-of",
            "json",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=True,
        timeout=120,
    )
    return json.loads(result.stdout).get("streams", [])


def describe(stream: dict) -> str:
    flags = []
    if disposition(stream, "default"):
        flags.append("default")
    if is_forced(stream):
        flags.append("forced")
    if disposition(stream, "hearing_impaired"):
        flags.append("sdh")
    flag_text = ",".join(flags) if flags else "no-flags"
    title = stream_tag(stream, "title")
    title_text = f':"{title}"' if title else ""
    return (
        f"track {stream.get('index', '?')}:"
        f"{stream.get('codec_name', 'unknown')}:"
        f"{stream_tag(stream, 'language', 'und')}:"
        f"{flag_text}{title_text}"
    )


def classify(streams: list[dict]) -> str:
    english = [stream for stream in streams if is_english(stream)]
    english_text = [stream for stream in english if is_text(stream)]
    qualifying = [
        stream
        for stream in english_text
        if disposition(stream, "default") and not is_forced(stream)
    ]
    if qualifying:
        english_defaults = [stream for stream in english if disposition(stream, "default")]
        if len(english_defaults) > 1:
            return "AMBIGUOUS_ENGLISH_DEFAULTS"
        return "OK_DEFAULT_NONFORCED_ENGLISH_TEXT"
    if any(not is_forced(stream) for stream in english_text):
        return "ENGLISH_TEXT_NOT_DEFAULT"
    if english_text:
        return "ONLY_FORCED_ENGLISH_TEXT"
    if english:
        return "ONLY_IMAGE_BASED_ENGLISH"
    return "NO_ENGLISH_SUBTITLES"


def videos_under(paths: list[Path]):
    seen: set[Path] = set()
    for root in paths:
        candidates = [root] if root.is_file() else root.rglob("*")
        for candidate in candidates:
            if not candidate.is_file() or candidate.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield resolved


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit for videos lacking a default, non-forced English text subtitle stream."
        )
    )
    parser.add_argument(
        "library", type=Path, nargs="+", help="One or more library roots or video files"
    )
    parser.add_argument("--report", type=Path, required=True, help="CSV report path")
    parser.add_argument("--limit", type=int, default=0, help="Inspect at most N videos")
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print progress every N videos (default: 100)",
    )
    args = parser.parse_args()

    roots = [path.expanduser().resolve() for path in args.library]
    for root in roots:
        if not root.exists():
            parser.error(f"Path does not exist: {root}")
        if root.is_file() and root.suffix.lower() not in VIDEO_EXTENSIONS:
            parser.error(f"Unsupported video extension: {root}")
    if not shutil.which("ffprobe"):
        parser.error("ffprobe is required")
    if args.limit < 0:
        parser.error("--limit cannot be negative")
    if args.progress_every < 1:
        parser.error("--progress-every must be at least 1")

    report = args.report.expanduser().resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    inspected = 0

    with report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()

        for video in videos_under(roots):
            inspected += 1
            if inspected == 1 or inspected % args.progress_every == 0:
                print(f"Progress: inspecting video {inspected}: {video}", flush=True)
            try:
                streams = probe(video)
                status = classify(streams)
                english = [stream for stream in streams if is_english(stream)]
                writer.writerow(
                    {
                        "video": str(video),
                        "status": status,
                        "english_subtitle_streams": "; ".join(map(describe, english)),
                        "all_subtitle_streams": "; ".join(map(describe, streams)),
                        "error": "",
                    }
                )
            except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as error:
                status = "PROBE_ERROR"
                detail = str(error)
                if isinstance(error, subprocess.CalledProcessError) and error.stderr:
                    detail = error.stderr.strip().splitlines()[-1]
                writer.writerow(
                    {
                        "video": str(video),
                        "status": status,
                        "english_subtitle_streams": "",
                        "all_subtitle_streams": "",
                        "error": detail,
                    }
                )
            counts[status] += 1
            if args.limit and inspected >= args.limit:
                break

    print("Summary")
    print(f"  Videos inspected: {inspected}")
    for status in (
        "OK_DEFAULT_NONFORCED_ENGLISH_TEXT",
        "AMBIGUOUS_ENGLISH_DEFAULTS",
        "ENGLISH_TEXT_NOT_DEFAULT",
        "ONLY_FORCED_ENGLISH_TEXT",
        "ONLY_IMAGE_BASED_ENGLISH",
        "NO_ENGLISH_SUBTITLES",
        "PROBE_ERROR",
    ):
        print(f"  {status}: {counts[status]}")
    print(f"  Report: {report}")
    print("  Read-only audit; no media, subtitle, or database files were changed.")
    return 1 if counts["PROBE_ERROR"] else 0


if __name__ == "__main__":
    sys.exit(main())
