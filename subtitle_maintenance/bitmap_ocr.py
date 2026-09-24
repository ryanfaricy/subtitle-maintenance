#!/usr/bin/env python3
"""OCR embedded bitmap subtitles and add text tracks to Matroska files.

The default mode is a read-only scan.  With --apply, each bitmap subtitle
track is OCR'd to a temporary SRT and appended to a newly remuxed MKV.  The
original bitmap tracks are retained.  The source file is replaced only after
the remux passes verification.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

FFPROBE = "ffprobe"

VIDEO_EXTENSIONS = {".mkv", ".mka", ".mks", ".mp4", ".m4v", ".mov", ".avi", ".ts", ".m2ts"}
MATROSKA_EXTENSIONS = {".mkv", ".mka", ".mks"}
BITMAP_CODECS = {
    "dvd_subtitle",  # DVD/VobSub
    "hdmv_pgs_subtitle",  # Blu-ray PGS
    "dvb_subtitle",  # DVB bitmap subtitles
    "xsub",  # DivX bitmap subtitles
}
TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "hdmv_text_subtitle"}
TEXT_SIDECAR_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".webvtt", ".ttml", ".dfxp", ".smi"}
ENGLISH_LANGUAGE_TAGS = {"en", "eng", "english"}
OCR_TITLE_PREFIX = "OCR text from bitmap track"
OCR_SOURCE_INDEX_RE = re.compile(r"^OCR text from bitmap track (\d+) \(")
SRT_TIMESTAMP_RE = re.compile(
    r"^\s*(-?\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(-?\d+):(\d{2}):(\d{2})[,.](\d{3})(?:\s+.*)?$"
)

# Apple Vision accepts BCP-47 tags. Subtitle Edit also maps many short/alpha3
# codes, but explicit mappings keep common library languages deterministic.
APPLE_VISION_LANGUAGES = {
    "eng": "en-US",
    "en": "en-US",
    "spa": "es-ES",
    "es": "es-ES",
    "fra": "fr-FR",
    "fre": "fr-FR",
    "fr": "fr-FR",
    "deu": "de-DE",
    "ger": "de-DE",
    "de": "de-DE",
    "ita": "it-IT",
    "it": "it-IT",
    "por": "pt-PT",
    "pt": "pt-PT",
    "nld": "nl-NL",
    "dut": "nl-NL",
    "nl": "nl-NL",
    "jpn": "ja-JP",
    "ja": "ja-JP",
    "kor": "ko-KR",
    "ko": "ko-KR",
    "zho": "zh-Hans",
    "chi": "zh-Hans",
    "zh": "zh-Hans",
}
TESSERACT_LANGUAGES = {
    "en": "eng",
    "eng": "eng",
    "es": "spa",
    "spa": "spa",
    "fr": "fra",
    "fre": "fra",
    "fra": "fra",
    "de": "deu",
    "ger": "deu",
    "deu": "deu",
    "it": "ita",
    "ita": "ita",
    "pt": "por",
    "por": "por",
    "nl": "nld",
    "dut": "nld",
    "nld": "nld",
    "ja": "jpn",
    "jpn": "jpn",
    "ko": "kor",
    "kor": "kor",
    "zh": "chi_sim",
    "chi": "chi_sim",
    "zho": "chi_sim",
}


def run(command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    command = [FFPROBE if str(x) == "ffprobe" and i == 0 else x for i, x in enumerate(command)]
    return subprocess.run(
        command,
        text=True,
        capture_output=capture,
        check=True,
    )


def probe(path: Path) -> list[dict]:
    result = run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)])
    return json.loads(result.stdout).get("streams", [])


def tag(stream: dict, name: str, default: str = "") -> str:
    tags = stream.get("tags") or {}
    for key, value in tags.items():
        if str(key).lower() == name.lower():
            return str(value)
    return default


def bitmap_tracks(streams: list[dict]) -> list[dict]:
    return [
        stream
        for stream in streams
        if stream.get("codec_type") == "subtitle" and stream.get("codec_name") in BITMAP_CODECS
    ]


def ocr_marker(stream: dict) -> str:
    return f"{OCR_TITLE_PREFIX} {stream['index']} ({stream.get('codec_name', 'bitmap')})"


def already_converted(stream: dict, streams: list[dict]) -> bool:
    marker = ocr_marker(stream)
    return any(
        candidate.get("codec_type") == "subtitle"
        and candidate.get("codec_name") in TEXT_CODECS
        and tag(candidate, "title") == marker
        for candidate in streams
    )


def is_generated_ocr_track(stream: dict) -> bool:
    return (
        stream.get("codec_type") == "subtitle"
        and stream.get("codec_name") in TEXT_CODECS
        and tag(stream, "title").startswith(OCR_TITLE_PREFIX)
    )


def generated_source_index(stream: dict) -> int | None:
    match = OCR_SOURCE_INDEX_RE.match(tag(stream, "title"))
    return int(match.group(1)) if match else None


def generated_from_forced_track(stream: dict, streams: list[dict]) -> bool:
    source_index = generated_source_index(stream)
    if source_index is None:
        return False
    return any(
        candidate.get("index") == source_index and is_forced_track(candidate)
        for candidate in streams
    )


def language_for_track(stream: dict) -> str:
    language = tag(stream, "language", "und").lower()
    return language


def is_english_language(language: str) -> bool:
    language = language.lower()
    return language in ENGLISH_LANGUAGE_TAGS or language.startswith("en-")


def language_is_selected(language: str, allowed_languages: set[str] | None) -> bool:
    if allowed_languages is None or language in allowed_languages:
        return True
    return is_english_language(language) and bool(allowed_languages & ENGLISH_LANGUAGE_TAGS)


def is_forced_track(stream: dict) -> bool:
    disposition = stream.get("disposition") or {}
    if bool(disposition.get("forced")):
        return True
    title_words = {word.strip("[](){}._-").lower() for word in tag(stream, "title").split()}
    return "forced" in title_words


def is_english_text_track(stream: dict) -> bool:
    if (
        stream.get("codec_type") != "subtitle"
        or stream.get("codec_name") not in TEXT_CODECS
        or is_forced_track(stream)
    ):
        return False
    language = language_for_track(stream)
    title_words = {word.strip("[](){}._-").lower() for word in tag(stream, "title").split()}
    return is_english_language(language) or bool(title_words & ENGLISH_LANGUAGE_TAGS)


def english_text_sidecars(video: Path) -> list[Path]:
    """Find matching sidecars that are explicitly English or have no language suffix."""
    matches: list[Path] = []
    stem_lower = video.stem.lower()
    prefix = stem_lower + "."
    for candidate in video.parent.iterdir():
        if not candidate.is_file() or candidate.suffix.lower() not in TEXT_SIDECAR_EXTENSIONS:
            continue
        candidate_stem = candidate.stem.lower()
        if candidate_stem == stem_lower:
            # A same-basename text sidecar conventionally belongs to this video;
            # in this English-only workflow, treat an unlabelled sidecar as English.
            matches.append(candidate)
            continue
        if not candidate_stem.startswith(prefix):
            continue
        qualifiers = {
            part
            for part in candidate_stem[len(prefix) :].replace("-", ".").replace("_", ".").split(".")
            if part
        }
        if "forced" in qualifiers:
            continue
        if any(is_english_language(qualifier) for qualifier in qualifiers):
            matches.append(candidate)
    return sorted(matches)


def ocr_language_for_track(stream: dict, engine: str) -> str:
    language = language_for_track(stream)
    if engine == "applevision":
        return APPLE_VISION_LANGUAGES.get(language, "en-US")
    if engine == "tesseract":
        return TESSERACT_LANGUAGES.get(language, language if language != "und" else "eng")
    return language if language != "und" else "English"


def find_single_srt(folder: Path) -> Path:
    candidates = list(folder.rglob("*.srt"))
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one OCR output SRT, found {len(candidates)} in {folder}"
        )
    output = candidates[0]
    if output.stat().st_size == 0:
        raise RuntimeError("OCR output is empty")
    return output


def srt_time_ms(parts: tuple[str, str, str, str]) -> int:
    hours_text, minutes_text, seconds_text, milliseconds_text = parts
    sign = -1 if hours_text.startswith("-") else 1
    hours = abs(int(hours_text))
    total = ((hours * 60 + int(minutes_text)) * 60 + int(seconds_text)) * 1000 + int(
        milliseconds_text
    )
    return sign * total


def format_srt_time(milliseconds: int) -> str:
    milliseconds = max(0, milliseconds)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def normalize_srt(path: Path) -> int:
    """Rewrite OCR SRT into canonical cues and clamp negative timestamps."""
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    timestamp_lines = [index for index, line in enumerate(lines) if SRT_TIMESTAMP_RE.match(line)]
    if not timestamp_lines:
        raise RuntimeError("OCR output contains no valid SRT timestamp lines")

    cues: list[tuple[int, int, list[str]]] = []
    for cue_number, timestamp_index in enumerate(timestamp_lines):
        match = SRT_TIMESTAMP_RE.match(lines[timestamp_index])
        assert match
        start_ms = srt_time_ms(match.groups()[0:4])
        end_ms = srt_time_ms(match.groups()[4:8])
        start_ms = max(0, start_ms)
        end_ms = max(start_ms + 1, end_ms)

        next_timestamp = (
            timestamp_lines[cue_number + 1] if cue_number + 1 < len(timestamp_lines) else len(lines)
        )
        text_lines = lines[timestamp_index + 1 : next_timestamp]
        while text_lines and not text_lines[-1].strip():
            text_lines.pop()
        # The numeric index belonging to the next cue sits immediately before
        # its timestamp and is not caption text.
        if (
            cue_number + 1 < len(timestamp_lines)
            and text_lines
            and text_lines[-1].strip().isdigit()
        ):
            text_lines.pop()
            while text_lines and not text_lines[-1].strip():
                text_lines.pop()
        while text_lines and not text_lines[0].strip():
            text_lines.pop(0)
        if not any(line.strip() for line in text_lines):
            continue
        cues.append((start_ms, end_ms, text_lines))

    if not cues:
        raise RuntimeError("OCR output contains no non-empty SRT cues")

    normalized: list[str] = []
    for index, (start_ms, end_ms, text_lines) in enumerate(cues, start=1):
        normalized.extend(
            [
                str(index),
                f"{format_srt_time(start_ms)} --> {format_srt_time(end_ms)}",
                *text_lines,
                "",
            ]
        )
    path.write_text("\n".join(normalized), encoding="utf-8")
    return len(cues)


def no_ocr_text(details: str) -> bool:
    """Return true when Subtitle Edit found images but no usable text."""
    message = details.lower()
    return (
        "produced no ocr text" in message
        or "ocr output is empty" in message
        or "no pgs subtitles in mkv track" in message
        or (
            "no subtitle tracks matched" in message
            and ("1 image(s)" in message or "0 image(s)" in message)
        )
    )


def ocr_track(
    video: Path,
    stream: dict,
    folder: Path,
    seconv: str,
    engine: str,
) -> Path | None:
    language = language_for_track(stream)
    ocr_language = ocr_language_for_track(stream, engine)
    track_number = int(stream["index"]) + 1  # Subtitle Edit container track numbers are one-based.
    output_folder = folder / f"track-{stream['index']}"
    output_folder.mkdir()

    command = [
        seconv,
        str(video),
        "subrip",
        f"--track-number:{track_number}",
        f"--ocr-engine:{engine}",
        f"--ocr-language:{ocr_language}",
        f"--output-folder:{output_folder}",
        "--overwrite",
    ]
    print(f"    OCR track {stream['index']} ({stream.get('codec_name')}, {language})", flush=True)
    try:
        result = run(command)
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or "").strip()
        if no_ocr_text(details):
            print("      SKIP: bitmap track produced no recognizable OCR text", flush=True)
            return None
        raise RuntimeError(f"Subtitle OCR failed: {details}") from error
    if result.stdout.strip():
        print("      " + result.stdout.strip().replace("\n", "\n      "))
    try:
        subtitle = find_single_srt(output_folder)
        cue_count = normalize_srt(subtitle)
        print(f"      Normalized and validated {cue_count} SRT cue(s)", flush=True)
        return subtitle
    except RuntimeError as error:
        details = f"{result.stdout}\n{result.stderr}\n{error}"
        if no_ocr_text(details):
            print("      SKIP: bitmap track produced no recognizable OCR text", flush=True)
            return None
        raise


def verify_output(
    output: Path,
    original_streams: list[dict],
    added_count: int,
    removed_titles: set[str],
) -> None:
    output_streams = probe(output)
    original_bitmap_count = len(bitmap_tracks(original_streams))
    output_bitmap_count = len(bitmap_tracks(output_streams))
    if output_bitmap_count != original_bitmap_count:
        raise RuntimeError(
            f"Verification failed: bitmap track count changed "
            f"({original_bitmap_count} -> {output_bitmap_count})"
        )

    original_text_count = sum(
        1
        for stream in original_streams
        if stream.get("codec_type") == "subtitle" and stream.get("codec_name") in TEXT_CODECS
    )
    output_text_count = sum(
        1
        for stream in output_streams
        if stream.get("codec_type") == "subtitle" and stream.get("codec_name") in TEXT_CODECS
    )
    expected_text_count = original_text_count - len(removed_titles) + added_count
    if output_text_count < expected_text_count:
        raise RuntimeError(
            f"Verification failed: expected at least {expected_text_count} "
            f"text tracks, found {output_text_count}"
        )

    output_titles = {tag(stream, "title") for stream in output_streams}
    remaining_removed_titles = removed_titles & output_titles
    if remaining_removed_titles:
        raise RuntimeError(
            "Verification failed: requested OCR track(s) remain: "
            + ", ".join(sorted(remaining_removed_titles))
        )


def remux(
    video: Path,
    tracks: list[tuple[dict, Path]],
    folder: Path,
    mkvmerge: str,
    remove_titles: set[str] | None = None,
) -> None:
    remove_titles = remove_titles or set()
    source_stat = video.stat()
    free_bytes = shutil.disk_usage(video.parent).free
    required_bytes = source_stat.st_size + 512 * 1024 * 1024
    if free_bytes < required_bytes:
        raise RuntimeError(
            f"Insufficient free space: need approximately {required_bytes / 2**30:.1f} GiB"
        )

    output = folder / f"{video.stem}.ocr-remux.mkv"
    command = [mkvmerge, "--output", str(output)]
    if remove_titles:
        identification = json.loads(run([mkvmerge, "-J", str(video)]).stdout)
        subtitle_tracks = [
            track for track in identification.get("tracks", []) if track.get("type") == "subtitles"
        ]
        kept_subtitle_ids = [
            str(track["id"])
            for track in subtitle_tracks
            if (track.get("properties") or {}).get("track_name", "") not in remove_titles
        ]
        if kept_subtitle_ids:
            command.extend(["--subtitle-tracks", ",".join(kept_subtitle_ids)])
        else:
            command.append("--no-subtitles")
    command.append(str(video))
    for stream, subtitle in tracks:
        language = language_for_track(stream)
        command.extend(
            [
                "--language",
                f"0:{language}",
                "--track-name",
                f"0:{ocr_marker(stream)}",
                "--default-track-flag",
                "0:no",
                str(subtitle),
            ]
        )

    actions = []
    if tracks:
        actions.append(f"adding {len(tracks)} text track(s)")
    if remove_titles:
        actions.append(f"removing {len(remove_titles)} redundant OCR track(s)")
    print(f"    Remuxing: {', '.join(actions)}", flush=True)
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    details = (result.stderr or result.stdout or "").strip()
    # mkvmerge uses 0 for success, 1 for success with warnings, and 2 for
    # failure. Warning outputs are still subjected to the same ffprobe checks.
    if result.returncode >= 2:
        raise RuntimeError(f"MKV remux failed: {details}")
    if result.returncode == 1:
        warning_lines = [line for line in details.splitlines() if "warning" in line.lower()]
        print(
            "      mkvmerge completed with warning(s); verifying output: "
            + (" | ".join(warning_lines) if warning_lines else details),
            flush=True,
        )

    original_streams = probe(video)
    verify_output(output, original_streams, len(tracks), remove_titles)

    source_mode = stat.S_IMODE(source_stat.st_mode)
    os.chmod(output, source_mode)
    os.replace(output, video)
    os.utime(video, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
    print(
        "    VERIFIED AND REPLACED (original bitmap tracks and file timestamps retained)",
        flush=True,
    )


def bitmap_has_payload(video: Path, stream: dict) -> bool:
    """Distinguish real bitmap payloads from PGS control/clear-only streams."""
    try:
        result = run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                str(stream["index"]),
                "-show_entries",
                "packet=size",
                "-of",
                "csv=p=0",
                str(video),
            ]
        )
    except subprocess.CalledProcessError:
        return True  # Ambiguous probe results should be reviewed/processed, not hidden.
    sizes = [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]
    return any(size > 64 for size in sizes)


def audit_file(video: Path) -> dict[str, str]:
    row = {
        "path": str(video),
        "status": "",
        "details": "",
        "native_english_text": "0",
        "english_sidecars": "0",
        "generated_ocr": "0",
        "full_english_bitmap": "0",
        "forced_english_bitmap": "0",
        "unknown_bitmap": "0",
        "cleanup_candidates": "0",
    }
    try:
        streams = probe(video)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as error:
        row["status"] = "INSPECTION_ERROR"
        row["details"] = str(error)
        return row

    bitmaps = bitmap_tracks(streams)
    native_text = [
        stream
        for stream in streams
        if is_english_text_track(stream) and not is_generated_ocr_track(stream)
    ]
    sidecars = english_text_sidecars(video)
    generated = [stream for stream in streams if is_generated_ocr_track(stream)]
    generated_forced = [
        stream for stream in generated if generated_from_forced_track(stream, streams)
    ]
    generated_full = [stream for stream in generated if stream not in generated_forced]
    full_english_bitmap = [
        stream
        for stream in bitmaps
        if is_english_language(language_for_track(stream)) and not is_forced_track(stream)
    ]
    forced_english_bitmap = [
        stream
        for stream in bitmaps
        if is_english_language(language_for_track(stream)) and is_forced_track(stream)
    ]
    unknown_bitmap = [
        stream
        for stream in bitmaps
        if language_for_track(stream) == "und" and not is_forced_track(stream)
    ]
    cleanup = list(generated_forced)
    if native_text:
        cleanup.extend(generated)
    cleanup = list({stream["index"]: stream for stream in cleanup}.values())

    row.update(
        {
            "native_english_text": str(len(native_text)),
            "english_sidecars": str(len(sidecars)),
            "generated_ocr": str(len(generated)),
            "full_english_bitmap": str(len(full_english_bitmap)),
            "forced_english_bitmap": str(len(forced_english_bitmap)),
            "unknown_bitmap": str(len(unknown_bitmap)),
            "cleanup_candidates": str(len(cleanup)),
        }
    )

    if cleanup:
        row["status"] = "CLEANUP_CANDIDATE"
        reasons = []
        if native_text and generated:
            reasons.append("original embedded English text exists")
        if generated_forced:
            reasons.append("generated from forced-only bitmap")
        row["details"] = "; ".join(reasons)
    elif native_text:
        row["status"] = "SATISFIED_EMBEDDED_TEXT"
    elif sidecars:
        row["status"] = "SATISFIED_SIDECAR"
        if generated:
            row["details"] = "generated OCR retained because sidecars are not trusted for cleanup"
    elif generated_full:
        row["status"] = "SATISFIED_GENERATED_OCR"
    elif full_english_bitmap:
        usable = [stream for stream in full_english_bitmap if bitmap_has_payload(video, stream)]
        if not usable:
            row["status"] = "NO_USABLE_BITMAP_PAYLOAD"
            row["details"] = "English bitmap streams contain only control/clear packets"
        elif video.suffix.lower() not in MATROSKA_EXTENSIONS:
            row["status"] = "UNSUPPORTED_CONTAINER"
            row["details"] = "full English bitmap exists but automatic embedding is Matroska-only"
        else:
            row["status"] = "NEEDS_OCR"
    elif unknown_bitmap:
        row["status"] = "NEEDS_REVIEW_UNKNOWN_BITMAP"
        row["details"] = "bitmap language is unknown; not safe to assume English"
    elif forced_english_bitmap:
        row["status"] = "NO_FULL_ENGLISH_FORCED_ONLY"
    else:
        row["status"] = "NO_ENGLISH_SUBTITLE_SOURCE"
    return row


def write_audit(library: Path, report: Path, limit: int) -> int:
    report.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "path",
        "status",
        "details",
        "native_english_text",
        "english_sidecars",
        "generated_ocr",
        "full_english_bitmap",
        "forced_english_bitmap",
        "unknown_bitmap",
        "cleanup_candidates",
    ]
    counts: dict[str, int] = {}
    inspected = 0
    candidates = [library] if library.is_file() else library.rglob("*")
    with report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for video in candidates:
            if not video.is_file() or video.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            inspected += 1
            if inspected == 1 or inspected % 100 == 0:
                print(f"Audit progress: inspected {inspected} video file(s)...", flush=True)
            row = audit_file(video)
            writer.writerow(row)
            counts[row["status"]] = counts.get(row["status"], 0) + 1
            if limit and inspected >= limit:
                break

    print("Audit summary")
    print(f"  Videos inspected: {inspected}")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")
    print(f"  Report: {report}")
    return 1 if counts.get("INSPECTION_ERROR", 0) else 0


def process_file(
    video: Path,
    apply: bool,
    seconv: str | None,
    mkvmerge: str | None,
    ocr_engine: str | None,
    allowed_languages: set[str] | None,
    include_unknown: bool,
    skip_if_english_text: bool,
    include_forced: bool,
    remove_redundant_ocr_text: bool,
    remove_forced_ocr_text: bool,
) -> tuple[int, int, int]:
    try:
        streams = probe(video)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"ERROR: cannot inspect {video}: {error}", flush=True)
        return 0, 0, 1

    tracks = bitmap_tracks(streams)
    generated_ocr_tracks = [stream for stream in streams if is_generated_ocr_track(stream)]
    native_english_text = [
        stream
        for stream in streams
        if is_english_text_track(stream) and not is_generated_ocr_track(stream)
    ]
    redundant_ocr_tracks = []
    if remove_redundant_ocr_text and native_english_text:
        redundant_ocr_tracks.extend(generated_ocr_tracks)
    if remove_forced_ocr_text:
        redundant_ocr_tracks.extend(
            stream
            for stream in generated_ocr_tracks
            if generated_from_forced_track(stream, streams)
        )
    redundant_ocr_tracks = list(
        {stream["index"]: stream for stream in redundant_ocr_tracks}.values()
    )

    if redundant_ocr_tracks:
        print(f"REDUNDANT OCR TEXT: {video}")
        for stream in native_english_text:
            print(
                f"  Keeping original embedded English text: track={stream['index']} "
                f"codec={stream.get('codec_name')} title={tag(stream, 'title') or '-'}"
            )
        for stream in redundant_ocr_tracks:
            reason = (
                "forced-only source"
                if generated_from_forced_track(stream, streams)
                else "original embedded English text exists"
            )
            print(
                f"  {'REMOVE' if apply else 'WOULD REMOVE'} generated OCR text: "
                f"track={stream['index']} title={tag(stream, 'title')} reason={reason}"
            )

        if video.suffix.lower() not in MATROSKA_EXTENSIONS:
            print("  SKIP: automatic remux is limited to Matroska files.\n", flush=True)
            return 0, 0, 1 if apply else 0
        if not apply:
            print("  Preview only; source file was not changed.\n", flush=True)
            return 0, len(redundant_ocr_tracks), 0

        assert mkvmerge
        try:
            with tempfile.TemporaryDirectory(
                prefix=f".{video.stem}.ocr-cleanup-", dir=video.parent
            ) as temp:
                remux(
                    video,
                    [],
                    Path(temp),
                    mkvmerge,
                    {tag(stream, "title") for stream in redundant_ocr_tracks},
                )
        except Exception as error:
            print(f"  ERROR: {error}\n  Source file was not replaced.\n", flush=True)
            return 0, 0, 1
        print()
        return 0, len(redundant_ocr_tracks), 0

    if not tracks:
        return 0, 0, 0

    embedded_english_text = [stream for stream in streams if is_english_text_track(stream)]
    sidecar_english_text = english_text_sidecars(video)
    skip_for_existing_text = skip_if_english_text and bool(
        embedded_english_text or sidecar_english_text
    )
    unconverted = [stream for stream in tracks if not already_converted(stream, streams)]
    eligible = [
        stream
        for stream in unconverted
        if (include_forced or not is_forced_track(stream))
        and (
            (language_for_track(stream) == "und" and include_unknown)
            or (
                language_for_track(stream) != "und"
                and (language_is_selected(language_for_track(stream), allowed_languages))
            )
        )
    ]
    empty_payload_indexes = {
        stream["index"]
        for stream in eligible
        if not skip_for_existing_text and not bitmap_has_payload(video, stream)
    }
    pending = (
        []
        if skip_for_existing_text
        else [stream for stream in eligible if stream["index"] not in empty_payload_indexes]
    )
    print(f"BITMAP SUBTITLES: {video}")
    for stream in tracks:
        language = language_for_track(stream)
        if already_converted(stream, streams):
            status = "already has generated OCR track"
        elif is_forced_track(stream) and not include_forced:
            status = "skipped (forced-only track)"
        elif stream["index"] in empty_payload_indexes:
            status = "skipped (no bitmap image payload)"
        elif skip_for_existing_text:
            status = "skipped (English text subtitles already exist)"
        elif stream in pending:
            status = "needs OCR"
        elif language == "und":
            status = "skipped (unknown language)"
        else:
            status = "skipped (language not selected)"
        print(
            f"  track={stream['index']} codec={stream.get('codec_name')} "
            f"language={language} status={status}"
        )

    if skip_for_existing_text:
        for stream in embedded_english_text:
            print(
                f"  Existing embedded English text: track={stream['index']} "
                f"codec={stream.get('codec_name')} title={tag(stream, 'title') or '-'}"
            )
        for sidecar in sidecar_english_text:
            print(f"  Existing English text sidecar: {sidecar.name}")
        print("  No action needed.\n", flush=True)
        return 0, 0, 0

    if not pending:
        print("  No action needed.\n", flush=True)
        return 0, 0, 0

    if video.suffix.lower() not in MATROSKA_EXTENSIONS:
        print("  SKIP: automatic remux is limited to Matroska files.\n", flush=True)
        return 0, 0, 1 if apply else 0

    if not apply:
        print(
            f"  WOULD OCR AND ADD: {len(pending)} text track(s); bitmap tracks retained.\n",
            flush=True,
        )
        return len(pending), 0, 0

    assert seconv and mkvmerge and ocr_engine
    try:
        with tempfile.TemporaryDirectory(prefix=f".{video.stem}.ocr-", dir=video.parent) as temp:
            temp_path = Path(temp)
            converted: list[tuple[dict, Path]] = []
            skipped_empty = 0
            for stream in pending:
                subtitle = ocr_track(video, stream, temp_path, seconv, ocr_engine)
                if subtitle is None:
                    skipped_empty += 1
                    continue
                converted.append((stream, subtitle))

            if not converted:
                print(
                    f"  No text tracks added; {skipped_empty} bitmap track(s) "
                    "contained no recognizable text.\n",
                    flush=True,
                )
                return 0, 0, 0
            remux(video, converted, temp_path, mkvmerge)
    except Exception as error:
        print(f"  ERROR: {error}\n  Source file was not replaced.\n", flush=True)
        return 0, 0, 1

    print()
    return len(converted), 0, 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find embedded bitmap subtitles and optionally OCR them into appended MKV text tracks."
    )
    parser.add_argument("library", type=Path, help="Media library root or one media file")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform OCR/remux. Without this flag, the script is read-only.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after this many video files (0 means no limit). Useful for testing.",
    )
    parser.add_argument("--ffprobe", default="ffprobe", help="ffprobe executable")
    parser.add_argument("--seconv", help="Path to Subtitle Edit's seconv executable")
    parser.add_argument("--mkvmerge", help="Path to the mkvmerge executable")
    parser.add_argument(
        "--ocr-engine",
        choices=("auto", "applevision", "tesseract"),
        default="auto",
        help="OCR engine (default: choose the first available supported engine)",
    )
    parser.add_argument(
        "--languages",
        default="eng",
        help="Comma-separated embedded subtitle languages to OCR, or 'all' (default: eng)",
    )
    parser.add_argument(
        "--include-unknown",
        action="store_true",
        help="OCR tracks whose language is unknown; they use English OCR unless identified manually.",
    )
    parser.add_argument(
        "--skip-if-english-text",
        action="store_true",
        help=(
            "Do not OCR a file when it already has an embedded English text subtitle "
            "or a matching English/unlabelled text sidecar."
        ),
    )
    parser.add_argument(
        "--include-forced",
        action="store_true",
        help="Also OCR forced-only bitmap tracks (excluded by default).",
    )
    parser.add_argument(
        "--remove-redundant-ocr-text",
        action="store_true",
        help=(
            "Remove this script's generated OCR text tracks when a separate, "
            "non-forced embedded English text track already exists."
        ),
    )
    parser.add_argument(
        "--remove-forced-ocr-text",
        action="store_true",
        help="Remove generated OCR text tracks whose source bitmap track is forced-only.",
    )
    parser.add_argument(
        "--audit-report",
        type=Path,
        help="Write a read-only per-video subtitle compliance audit to this CSV file.",
    )
    args = parser.parse_args()

    if args.audit_report and args.apply:
        parser.error("--audit-report is read-only and cannot be combined with --apply")

    library = args.library.expanduser().resolve()
    if not library.exists():
        parser.error(f"Path does not exist: {library}")
    if library.is_file() and library.suffix.lower() not in VIDEO_EXTENSIONS:
        parser.error(f"Unsupported media file: {library}")

    if args.languages.strip().lower() == "all":
        allowed_languages = None
    else:
        allowed_languages = {
            item.strip().lower() for item in args.languages.split(",") if item.strip()
        }
        if not allowed_languages:
            parser.error("--languages must contain at least one language or 'all'")

    global FFPROBE
    FFPROBE = args.ffprobe
    if not shutil.which(FFPROBE):
        parser.error("ffprobe is required")

    if args.audit_report:
        report = args.audit_report.expanduser().resolve()
        return write_audit(library, report, args.limit)

    bundled_seconv = Path(__file__).resolve().parent.parent / "seconv-5.2.0/seconv"
    seconv = (
        args.seconv
        or shutil.which("seconv")
        or (str(bundled_seconv) if bundled_seconv.is_file() else None)
    )
    mkvmerge = args.mkvmerge or shutil.which("mkvmerge")
    if seconv:
        seconv = str(Path(seconv).expanduser())
    if mkvmerge:
        mkvmerge = str(Path(mkvmerge).expanduser())
    if args.apply and (not seconv or not mkvmerge):
        missing = [name for name, path in (("seconv", seconv), ("mkvmerge", mkvmerge)) if not path]
        parser.error(
            "Apply mode requires missing tool(s): "
            + ", ".join(missing)
            + ". Install Subtitle Edit 5 CLI and MKVToolNix first."
        )

    ocr_engine = None
    if args.apply:
        try:
            engine_result = run([seconv, "list-ocr-engines", "--json"])
            engine_data = json.loads(engine_result.stdout)
            ready_engines = {
                item.get("id")
                for item in engine_data.get("engines", [])
                if item.get("ready") is not False
            }
        except (subprocess.CalledProcessError, json.JSONDecodeError) as error:
            parser.error(f"Could not query seconv OCR engines: {error}")

        if args.ocr_engine == "auto":
            ocr_engine = next(
                (name for name in ("applevision", "tesseract") if name in ready_engines),
                None,
            )
        else:
            ocr_engine = args.ocr_engine if args.ocr_engine in ready_engines else None
        if not ocr_engine:
            parser.error(
                f"Requested OCR engine is unavailable. Ready engines: {', '.join(sorted(ready_engines)) or 'none'}"
            )
        print(f"Using OCR engine: {ocr_engine}", flush=True)

    scanned = found_files = proposed_tracks = removed_tracks = errors = 0
    candidates = [library] if library.is_file() else library.rglob("*")
    for video in candidates:
        if not video.is_file() or video.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        scanned += 1
        if scanned == 1 or scanned % 25 == 0:
            print(f"Progress: inspected {scanned} video file(s)...", flush=True)
        proposed, removed, file_errors = process_file(
            video,
            args.apply,
            seconv,
            mkvmerge,
            ocr_engine,
            allowed_languages,
            args.include_unknown,
            args.skip_if_english_text,
            args.include_forced,
            args.remove_redundant_ocr_text,
            args.remove_forced_ocr_text,
        )
        proposed_tracks += proposed
        removed_tracks += removed
        errors += file_errors
        if proposed or removed or file_errors:
            found_files += 1
        if args.limit and scanned >= args.limit:
            break

    print("Summary")
    print(f"  Videos inspected: {scanned}")
    print(f"  Files needing attention: {found_files}")
    print(f"  OCR text tracks {'added' if args.apply else 'proposed'}: {proposed_tracks}")
    print(
        f"  Redundant OCR tracks {'removed' if args.apply else 'proposed for removal'}: {removed_tracks}"
    )
    print(f"  Errors/skips: {errors}")
    if not args.apply:
        print("  Preview only; no files were changed.")
    return 1 if errors and args.apply else 0


if __name__ == "__main__":
    sys.exit(main())
