#!/usr/bin/env python3
"""Read-only audit for redundant English forced subtitle tracks.

The audit compares each English forced text track with the strongest English
non-forced text track available to the same video. Embedded tracks and
same-stem text sidecars are inspected. Nothing is changed or deleted.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
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
TEXT_SUBTITLE_EXTENSIONS = {".ass", ".srt", ".ssa", ".vtt"}
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
    "forced_source",
    "forced_track",
    "forced_codec",
    "forced_title",
    "forced_default",
    "forced_cues",
    "forced_first_seconds",
    "forced_last_seconds",
    "full_source",
    "full_track",
    "full_codec",
    "full_title",
    "full_default",
    "full_sdh",
    "full_cues",
    "full_first_seconds",
    "full_last_seconds",
    "full_span_percent",
    "full_to_forced_ratio",
    "reason",
    "error",
]


@dataclass
class Track:
    source: str
    locator: str
    codec: str
    title: str
    default: bool
    forced: bool
    sdh: bool
    path: Path
    stream_index: int | None = None
    cues: int | None = None
    first: float | None = None
    last: float | None = None
    error: str = ""


def tag(stream: dict, name: str, default: str = "") -> str:
    for key, value in (stream.get("tags") or {}).items():
        if str(key).lower() == name.lower():
            return str(value)
    return default


def words(value: str) -> set[str]:
    cleaned = value.lower()
    for character in "[](){}._-":
        cleaned = cleaned.replace(character, " ")
    return {word for word in cleaned.split() if word}


def disposition(stream: dict, name: str) -> bool:
    return bool((stream.get("disposition") or {}).get(name))


def is_english_stream(stream: dict) -> bool:
    language = tag(stream, "language").strip().lower().replace("_", "-")
    return (
        language in ENGLISH_TAGS
        or language.startswith("en-")
        or bool(words(tag(stream, "title")) & ENGLISH_TAGS)
    )


def is_forced_stream(stream: dict) -> bool:
    return disposition(stream, "forced") or "forced" in words(tag(stream, "title"))


def is_sdh_stream(stream: dict) -> bool:
    return disposition(stream, "hearing_impaired") or bool(
        words(tag(stream, "title")) & {"sdh", "cc", "hi"}
    )


def probe_video(path: Path) -> tuple[float | None, list[dict]]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "s",
            "-show_entries",
            "format=duration:stream=index,codec_name:stream_tags=language,title:"
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
    payload = json.loads(result.stdout)
    duration_text = (payload.get("format") or {}).get("duration")
    duration = float(duration_text) if duration_text not in (None, "N/A") else None
    return duration, payload.get("streams", [])


def packet_metrics(track: Track) -> None:
    command = ["ffprobe", "-v", "error"]
    if track.source == "embedded":
        command += ["-select_streams", str(track.stream_index)]
    else:
        command += ["-select_streams", "s:0"]
    command += [
        "-show_packets",
        "-show_entries",
        "packet=pts_time,dts_time,duration_time",
        "-of",
        "json",
        str(track.path),
    ]
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=True,
            timeout=300,
        )
        packets = json.loads(result.stdout).get("packets", [])
        starts: list[float] = []
        ends: list[float] = []
        for packet in packets:
            raw_start = packet.get("pts_time", packet.get("dts_time"))
            if raw_start in (None, "N/A"):
                continue
            start = float(raw_start)
            raw_duration = packet.get("duration_time")
            duration = 0.0 if raw_duration in (None, "N/A") else float(raw_duration)
            starts.append(start)
            ends.append(start + max(0.0, duration))
        track.cues = len(packets)
        track.first = min(starts) if starts else None
        track.last = max(ends) if ends else None
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, ValueError) as error:
        track.error = command_error(error)


def sidecar_language_tokens(video: Path, sidecar: Path) -> set[str]:
    suffix = sidecar.name[len(video.stem) : -len(sidecar.suffix)]
    return words(suffix)


def same_stem_sidecars(video: Path) -> list[Track]:
    found: list[Track] = []
    prefix = video.stem + "."
    for sidecar in video.parent.iterdir():
        if not sidecar.is_file() or sidecar.suffix.lower() not in TEXT_SUBTITLE_EXTENSIONS:
            continue
        if not sidecar.name.startswith(prefix):
            continue
        tokens = sidecar_language_tokens(video, sidecar)
        if not tokens & ENGLISH_TAGS:
            continue
        found.append(
            Track(
                source="sidecar",
                locator=sidecar.name,
                codec=sidecar.suffix.lower().lstrip("."),
                title=sidecar.name,
                default=False,
                forced="forced" in tokens,
                sdh=bool(tokens & {"sdh", "cc", "hi"}),
                path=sidecar,
            )
        )
    return found


def embedded_tracks(video: Path, streams: list[dict]) -> list[Track]:
    tracks: list[Track] = []
    for stream in streams:
        codec = str(stream.get("codec_name", "")).lower()
        if codec not in TEXT_CODECS or not is_english_stream(stream):
            continue
        index = int(stream["index"])
        tracks.append(
            Track(
                source="embedded",
                locator=str(index),
                codec=codec,
                title=tag(stream, "title"),
                default=disposition(stream, "default"),
                forced=is_forced_stream(stream),
                sdh=is_sdh_stream(stream),
                path=video,
                stream_index=index,
            )
        )
    return tracks


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


def metric(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def command_error(error: BaseException) -> str:
    if isinstance(error, subprocess.CalledProcessError) and error.stderr:
        return error.stderr.strip().splitlines()[-1]
    return str(error)


def classify(
    forced: Track, full: Track, video_duration: float | None
) -> tuple[str, str, float | None, float | None]:
    if forced.error or full.error or forced.cues is None or full.cues is None:
        return "METRIC_ERROR", "Could not read cue/timestamp metrics", None, None
    ratio = float("inf") if forced.cues == 0 else full.cues / forced.cues
    span_percent = None
    if video_duration and video_duration > 0 and full.first is not None and full.last is not None:
        span_percent = max(0.0, full.last - full.first) / video_duration * 100.0
    if full.cues >= 100 and ratio >= 5.0 and span_percent is not None and span_percent >= 50.0:
        return (
            "SAFE_REDUNDANT_FORCED",
            "Strong non-forced full-dialogue candidate: >=100 cues, >=5x cue ratio, >=50% timeline span",
            ratio,
            span_percent,
        )
    if full.cues >= 50 and ratio >= 3.0 and span_percent is not None and span_percent >= 35.0:
        return (
            "PROBABLE_REDUNDANT_FORCED",
            "Likely redundant, but below conservative automatic-safety thresholds",
            ratio,
            span_percent,
        )
    return (
        "REVIEW_FORCED_WITH_NONFORCED",
        "A non-forced English text track exists, but coverage evidence is not decisive",
        ratio,
        span_percent,
    )


def row(
    video: Path,
    status: str,
    forced: Track | None = None,
    full: Track | None = None,
    ratio: float | None = None,
    span_percent: float | None = None,
    reason: str = "",
    error: str = "",
) -> dict[str, object]:
    return {
        "video": str(video),
        "status": status,
        "forced_source": forced.source if forced else "",
        "forced_track": forced.locator if forced else "",
        "forced_codec": forced.codec if forced else "",
        "forced_title": forced.title if forced else "",
        "forced_default": int(forced.default) if forced else "",
        "forced_cues": forced.cues if forced and forced.cues is not None else "",
        "forced_first_seconds": metric(forced.first) if forced else "",
        "forced_last_seconds": metric(forced.last) if forced else "",
        "full_source": full.source if full else "",
        "full_track": full.locator if full else "",
        "full_codec": full.codec if full else "",
        "full_title": full.title if full else "",
        "full_default": int(full.default) if full else "",
        "full_sdh": int(full.sdh) if full else "",
        "full_cues": full.cues if full and full.cues is not None else "",
        "full_first_seconds": metric(full.first) if full else "",
        "full_last_seconds": metric(full.last) if full else "",
        "full_span_percent": metric(span_percent),
        "full_to_forced_ratio": "inf" if ratio == float("inf") else metric(ratio),
        "reason": reason,
        "error": error,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only audit for redundant English forced text subtitles."
    )
    parser.add_argument("library", type=Path, nargs="+", help="Library roots or video files")
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
    candidate_files = 0

    with report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for video in videos_under(roots):
            inspected += 1
            if inspected == 1 or inspected % args.progress_every == 0:
                print(f"Progress: inspecting video {inspected}: {video}", flush=True)
            try:
                duration, streams = probe_video(video)
                tracks = embedded_tracks(video, streams) + same_stem_sidecars(video)
            except (subprocess.SubprocessError, json.JSONDecodeError, OSError, ValueError) as error:
                status = "PROBE_ERROR"
                writer.writerow(row(video, status, error=command_error(error)))
                counts[status] += 1
                if args.limit and inspected >= args.limit:
                    break
                continue

            forced_tracks = [track for track in tracks if track.forced]
            full_tracks = [track for track in tracks if not track.forced]
            if forced_tracks:
                candidate_files += 1
                print(f"FORCED ENGLISH TEXT: {video}", flush=True)
            if forced_tracks and not full_tracks:
                status = "KEEP_FORCED_ONLY"
                for forced in forced_tracks:
                    packet_metrics(forced)
                    writer.writerow(
                        row(
                            video,
                            status,
                            forced=forced,
                            reason="No English non-forced text track exists; do not remove",
                            error=forced.error,
                        )
                    )
                    counts[status] += 1
                    print(
                        f"  {forced.source} {forced.locator}: KEEP (no non-forced English text alternative)",
                        flush=True,
                    )
            elif forced_tracks:
                for track in forced_tracks + full_tracks:
                    packet_metrics(track)
                usable_full = [
                    track for track in full_tracks if track.cues is not None and not track.error
                ]
                best_full = max(
                    usable_full,
                    key=lambda track: (track.cues or 0, (track.last or 0) - (track.first or 0)),
                    default=None,
                )
                if best_full is None:
                    status = "METRIC_ERROR"
                    for forced in forced_tracks:
                        writer.writerow(
                            row(
                                video,
                                status,
                                forced=forced,
                                reason="Non-forced English track exists but its metrics could not be read",
                                error="; ".join(
                                    track.error for track in full_tracks if track.error
                                ),
                            )
                        )
                        counts[status] += 1
                else:
                    for forced in forced_tracks:
                        status, reason, ratio, span_percent = classify(forced, best_full, duration)
                        error = forced.error or best_full.error
                        writer.writerow(
                            row(
                                video,
                                status,
                                forced,
                                best_full,
                                ratio,
                                span_percent,
                                reason,
                                error,
                            )
                        )
                        counts[status] += 1
                        ratio_text = "inf" if ratio == float("inf") else metric(ratio)
                        print(
                            f"  {forced.source} {forced.locator}: {status} "
                            f"(forced={forced.cues}, full={best_full.cues}, ratio={ratio_text}, "
                            f"full-span={metric(span_percent)}%)",
                            flush=True,
                        )

            if args.limit and inspected >= args.limit:
                break

    print("Summary")
    print(f"  Videos inspected: {inspected}")
    print(f"  Videos with English forced text: {candidate_files}")
    for status in (
        "SAFE_REDUNDANT_FORCED",
        "PROBABLE_REDUNDANT_FORCED",
        "REVIEW_FORCED_WITH_NONFORCED",
        "KEEP_FORCED_ONLY",
        "METRIC_ERROR",
        "PROBE_ERROR",
    ):
        print(f"  {status}: {counts[status]}")
    print(f"  Report: {report}")
    print("  Read-only audit; no media or subtitle files were changed.")
    return 1 if counts["PROBE_ERROR"] or counts["METRIC_ERROR"] else 0


if __name__ == "__main__":
    sys.exit(main())
