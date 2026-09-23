#!/usr/bin/env python3
"""Create a read-only Markdown report of subtitle tracks and sample cues."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


TEXT_CODECS = {
    "ass", "dvb_teletext", "eia_608", "eia_708", "hdmv_text_subtitle",
    "microdvd", "mov_text", "mpl2", "realtext", "sami", "srt", "ssa",
    "subrip", "text", "ttml", "webvtt",
}


def tag(stream: dict, name: str, default: str = "") -> str:
    for key, value in (stream.get("tags") or {}).items():
        if str(key).lower() == name.lower():
            return str(value)
    return default


def flag(stream: dict, name: str) -> bool:
    return bool((stream.get("disposition") or {}).get(name))


def probe(path: Path) -> list[dict]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "s",
            "-show_entries",
            "stream=index,codec_name:stream_tags=language,title:"
            "stream_disposition=default,forced,hearing_impaired",
            "-of", "json", str(path),
        ],
        text=True, capture_output=True, check=True, timeout=120,
    )
    return json.loads(result.stdout).get("streams", [])


def cue_count(path: Path, index: int) -> int | None:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", str(index),
                "-count_packets", "-show_entries", "stream=nb_read_packets",
                "-of", "default=nw=1:nk=1", str(path),
            ],
            text=True, capture_output=True, check=True, timeout=300,
        )
        return int(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\{\\[^}]+\}", "", value)
    return " ".join(value.replace("\\N", " ").split())


def preview_cues(path: Path, index: int, codec: str, limit: int) -> list[str]:
    if codec not in TEXT_CODECS:
        return []
    with tempfile.TemporaryDirectory(prefix="subtitle-preview-") as folder:
        output = Path(folder) / "track.srt"
        try:
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(path), "-map", f"0:{index}", "-c:s", "srt",
                    str(output),
                ],
                text=True, capture_output=True, check=True, timeout=600,
            )
            content = output.read_text(encoding="utf-8-sig", errors="replace")
        except (subprocess.SubprocessError, OSError):
            return []
    blocks = re.split(r"\r?\n\s*\r?\n", content.strip())
    previews: list[str] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timestamp_at = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timestamp_at is None:
            continue
        text = clean_text(" ".join(lines[timestamp_at + 1:]))
        if not text:
            continue
        if len(text) > 180:
            text = text[:177] + "..."
        previews.append(f"{lines[timestamp_at]} — {text}")
        if len(previews) >= limit:
            break
    return previews


def main() -> int:
    parser = argparse.ArgumentParser(description="Report subtitle tracks and opening cue samples.")
    parser.add_argument("--plan", type=Path, required=True, help="Removal preview CSV")
    parser.add_argument("--report", type=Path, required=True, help="Markdown report path")
    parser.add_argument("--cues", type=int, default=3, help="Opening cues per text track")
    args = parser.parse_args()
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        parser.error("ffprobe and ffmpeg are required")
    plan = args.plan.expanduser().resolve()
    output = args.report.expanduser().resolve()
    with plan.open(newline="", encoding="utf-8") as handle:
        videos = [Path(row["video"]) for row in csv.DictReader(handle) if row["action"] == "WOULD_REMOVE"]

    lines = [
        "# Subtitle track and cue preview report", "",
        f"Files: {len(videos)}", "",
        "This is a read-only report. Cue samples are the first non-empty cues in each text track.", "",
    ]
    for number, video in enumerate(videos, 1):
        print(f"[{number}/{len(videos)}] {video}", flush=True)
        lines += [f"## {number}. {video.name}", "", f"`{video}`", ""]
        try:
            streams = probe(video)
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as error:
            lines += [f"Probe error: {error}", ""]
            continue
        for stream in streams:
            index = int(stream["index"])
            codec = str(stream.get("codec_name", "unknown")).lower()
            language = tag(stream, "language", "und")
            title = tag(stream, "title") or "(untitled)"
            flags = [
                name for name, enabled in (
                    ("default", flag(stream, "default")),
                    ("forced", flag(stream, "forced")),
                    ("SDH", flag(stream, "hearing_impaired")),
                ) if enabled
            ]
            count = cue_count(video, index)
            lines.append(
                f"### Track {index}: {language} · {codec} · {title}"
            )
            lines.append("")
            lines.append(f"Flags: {', '.join(flags) if flags else 'none'} · Cues/packets: {count if count is not None else 'unavailable'}")
            lines.append("")
            previews = preview_cues(video, index, codec, args.cues)
            if previews:
                for cue in previews:
                    lines.append(f"- {cue}")
            elif codec in TEXT_CODECS:
                lines.append("- Text preview unavailable")
            else:
                lines.append("- Image-based subtitle; text preview requires OCR")
            lines.append("")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report: {output}")
    print("Read-only; no media or subtitle files were changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
