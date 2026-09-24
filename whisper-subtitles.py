#!/usr/bin/env python3

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from script_config import load_config, state_path, tool

ASR_BASE_URL = os.getenv("WHISPER_ASR_URL", "http://127.0.0.1:9001").rstrip("/")
ASR_MODEL = "large-v3-turbo"

STATE_DIR = state_path({}, "WhisperSubtitles")
STATE_DB = STATE_DIR / "state.db"

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
CURL = "curl"

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".mov", ".avi", ".ts", ".m2ts", ".webm"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub"}

GRACE_HOURS_DEFAULT = 12
FAILED_RETRY_HOURS = 24
FFMPEG_TIMEOUT_SECONDS = 10 * 60
WHISPER_TIMEOUT_SECONDS = 30 * 60


def log(message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def connect_db():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(STATE_DB)
    db.execute("""
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            first_seen REAL NOT NULL,
            last_checked REAL NOT NULL,
            status TEXT NOT NULL,
            subtitle_path TEXT,
            error TEXT,
            failure_time REAL
        )
    """)
    db.commit()
    return db


def fingerprint(path):
    st = path.stat()
    return st.st_size, st.st_mtime_ns


def get_state(db, path):
    row = db.execute(
        """
        SELECT size, mtime_ns, first_seen, last_checked,
               status, subtitle_path, error, failure_time
        FROM files
        WHERE path = ?
    """,
        (str(path),),
    ).fetchone()
    if not row:
        return None
    return {
        "size": row[0],
        "mtime_ns": row[1],
        "first_seen": row[2],
        "last_checked": row[3],
        "status": row[4],
        "subtitle_path": row[5],
        "error": row[6],
        "failure_time": row[7],
    }


def save_state(
    db, path, size, mtime_ns, first_seen, status, subtitle_path=None, error=None, failure_time=None
):
    now = time.time()
    db.execute(
        """
        INSERT INTO files (
            path, size, mtime_ns, first_seen, last_checked,
            status, subtitle_path, error, failure_time
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            size = excluded.size,
            mtime_ns = excluded.mtime_ns,
            first_seen = excluded.first_seen,
            last_checked = excluded.last_checked,
            status = excluded.status,
            subtitle_path = excluded.subtitle_path,
            error = excluded.error,
            failure_time = excluded.failure_time
    """,
        (
            str(path),
            size,
            mtime_ns,
            first_seen,
            now,
            status,
            str(subtitle_path) if subtitle_path else None,
            error,
            failure_time,
        ),
    )
    db.commit()


def find_sidecar(video):
    stem = video.stem.lower()
    dirs = [
        video.parent,
        video.parent / "Subs",
        video.parent / "subs",
        video.parent / "Subtitles",
        video.parent / "subtitles",
    ]
    for directory in dirs:
        if not directory.is_dir():
            continue
        try:
            for candidate in directory.iterdir():
                if not candidate.is_file() or candidate.suffix.lower() not in SUBTITLE_EXTENSIONS:
                    continue
                name = candidate.stem.lower()
                if name == stem or name.startswith(stem + "."):
                    return candidate
        except OSError:
            continue
    return None


def has_audio_stream(video):
    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "json",
        str(video),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffprobe audio-stream check timed out after 120 seconds")

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe audio-stream check failed")

    data = json.loads(result.stdout or "{}")
    return len(data.get("streams", [])) > 0


def has_embedded_subtitle(video):
    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=index,codec_name:stream_tags=language,title",
        "-of",
        "json",
        str(video),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffprobe timed out after 120 seconds")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")
    return len(json.loads(result.stdout or "{}").get("streams", [])) > 0


def format_timestamp(seconds):
    seconds = max(0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        secs += 1
        millis = 0
    if secs == 60:
        minutes += 1
        secs = 0
    if minutes == 60:
        hours += 1
        minutes = 0
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def make_srt(segments):
    blocks = []
    for segment in segments:
        text = segment.get("text", "").strip()
        if not text:
            continue
        start = format_timestamp(segment.get("start", 0))
        end = format_timestamp(segment.get("end", 0))
        blocks.append(f"{len(blocks) + 1}\n{start} --> {end}\n{text}\n")
    return "\n".join(blocks)


def terminate_process_group(proc, grace_seconds=5):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


def run_ffmpeg_extract(video, wav, timeout_seconds):
    cmd = [
        FFMPEG,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(wav),
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=True
    )
    try:
        _, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        terminate_process_group(proc)
        raise RuntimeError(
            f"FFmpeg audio extraction timed out after {timeout_seconds / 60:g} minutes"
        )
    if proc.returncode != 0:
        raise RuntimeError(
            (stderr or "").strip() or f"FFmpeg failed with exit code {proc.returncode}"
        )
    if not wav.exists() or wav.stat().st_size == 0:
        raise RuntimeError("FFmpeg produced no audio")


def check_asr_service():
    result = subprocess.run(
        [CURL, "--silent", "--show-error", "--fail", "--max-time", "5", f"{ASR_BASE_URL}/health"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "service unavailable").strip()
        raise RuntimeError(f"Whisper ASR service unavailable at {ASR_BASE_URL}: {detail}")


def run_asr_transcription(wav, timeout_seconds):
    url = (
        f"{ASR_BASE_URL}/asr?task=transcribe&language=en&model={ASR_MODEL}"
        "&output_format=json&word_timestamps=false&diarize=false"
    )
    cmd = [
        CURL,
        "--silent",
        "--show-error",
        "--fail-with-body",
        "--max-time",
        str(max(1, int(timeout_seconds))),
        "--request",
        "POST",
        "--form",
        f"audio_file=@{wav};type=audio/wav",
        url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 15,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Whisper service request timed out after {timeout_seconds / 60:g} minutes"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "request failed").strip()[:2000]
        raise RuntimeError(f"Whisper service request failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Whisper service returned invalid JSON") from exc
    segments = payload.get("text")
    if not isinstance(segments, list):
        segments = payload.get("segments", [])
    return {"segments": segments, "language": payload.get("language", "en")}


def transcribe(video, ffmpeg_timeout_seconds, whisper_timeout_seconds):
    output = video.with_name(video.stem + ".en.srt")
    log(f"Extracting audio: {video.name}")
    with tempfile.TemporaryDirectory(prefix="whisper-subs-") as tmp:
        tmpdir = Path(tmp)
        wav = tmpdir / "audio.wav"
        run_ffmpeg_extract(video, wav, ffmpeg_timeout_seconds)
        log(f"Transcribing via {ASR_BASE_URL} with {ASR_MODEL}: {video.name}")
        started = time.time()
        result = run_asr_transcription(wav, whisper_timeout_seconds)
        elapsed = time.time() - started
        srt = make_srt(result.get("segments", []))
        if not srt.strip():
            raise RuntimeError("Whisper returned no subtitle segments")
        temp_output = output.with_suffix(output.suffix + ".tmp")
        temp_output.write_text(srt, encoding="utf-8")
        os.replace(temp_output, output)
        log(f"SUCCESS: {output.name} ({elapsed:.1f}s transcription time)")
    return output


def iter_videos(path):
    if path.is_file():
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            yield path
        return
    for item in path.rglob("*"):
        if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS:
            yield item


def process_file(db, video, args):
    try:
        size, mtime_ns = fingerprint(video)
    except OSError as e:
        log(f"SKIP unreadable: {video}: {e}")
        return False

    state = get_state(db, video)
    now = time.time()
    unchanged = state and state["size"] == size and state["mtime_ns"] == mtime_ns

    if unchanged and not args.rescan:
        status = state["status"]
        if status in {"embedded", "no_audio"}:
            return False
        if status in {"sidecar", "whisper_generated"}:
            subtitle_path = state["subtitle_path"]
            if subtitle_path and Path(subtitle_path).exists():
                return False
        if status == "failed":
            failure_time = state["failure_time"] or 0
            if now - failure_time < FAILED_RETRY_HOURS * 3600:
                return False
        if status == "missing":
            sidecar = find_sidecar(video)
            if sidecar:
                save_state(
                    db, video, size, mtime_ns, state["first_seen"], "sidecar", subtitle_path=sidecar
                )
                log(f"COVERED sidecar: {video.name}")
                return False
            first_seen = state["first_seen"]
            if not args.ignore_grace:
                media_age_hours = (now - (mtime_ns / 1_000_000_000)) / 3600
                if media_age_hours < args.grace_hours:
                    return False
            if args.scan_only:
                return False
            if args.dry_run:
                log(f"WOULD TRANSCRIBE: {video}")
                return False
            try:
                subtitle = transcribe(
                    video,
                    max(1, int(args.ffmpeg_timeout_minutes * 60)),
                    max(1, int(args.whisper_timeout_minutes * 60)),
                )
                save_state(
                    db,
                    video,
                    size,
                    mtime_ns,
                    first_seen,
                    "whisper_generated",
                    subtitle_path=subtitle,
                )
                return True
            except Exception as e:
                save_state(
                    db, video, size, mtime_ns, first_seen, "failed", error=str(e), failure_time=now
                )
                log(f"FAILED: {video}: {e}")
                return False

    first_seen = now
    log(f"CHECK: {video}")
    sidecar = find_sidecar(video)
    if sidecar:
        if not args.dry_run:
            save_state(db, video, size, mtime_ns, first_seen, "sidecar", subtitle_path=sidecar)
        log(f"COVERED sidecar: {sidecar.name}")
        return False

    try:
        if not has_audio_stream(video):
            if not args.dry_run:
                save_state(
                    db,
                    video,
                    size,
                    mtime_ns,
                    first_seen,
                    "no_audio",
                )

            log(f"NO AUDIO: {video.name}; permanently skipped until media changes")
            return False

        embedded = has_embedded_subtitle(video)
    except Exception as e:
        if not args.dry_run:
            save_state(
                db, video, size, mtime_ns, first_seen, "failed", error=str(e), failure_time=now
            )
        log(f"FAILED ffprobe: {video}: {e}")
        return False

    if embedded:
        if not args.dry_run:
            save_state(db, video, size, mtime_ns, first_seen, "embedded")
        log(f"COVERED embedded: {video.name}")
        return False

    if not args.dry_run:
        save_state(db, video, size, mtime_ns, first_seen, "missing")
    log(f"MISSING subtitles: {video.name}")

    if args.scan_only:
        return False
    if not args.ignore_grace:
        media_age_hours = (now - (mtime_ns / 1_000_000_000)) / 3600
        if media_age_hours < args.grace_hours:
            log(f"GRACE: {args.grace_hours - media_age_hours:.1f} hours remaining")
            return False
    if args.dry_run:
        log(f"WOULD TRANSCRIBE: {video}")
        return False

    try:
        subtitle = transcribe(
            video,
            max(1, int(args.ffmpeg_timeout_minutes * 60)),
            max(1, int(args.whisper_timeout_minutes * 60)),
        )
        save_state(
            db, video, size, mtime_ns, first_seen, "whisper_generated", subtitle_path=subtitle
        )
        return True
    except Exception as e:
        save_state(db, video, size, mtime_ns, first_seen, "failed", error=str(e), failure_time=now)
        log(f"FAILED: {video}: {e}")
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Production Whisper subtitle backfill: idempotent, 12-hour grace capable, and watchdog protected."
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--path", help="File or directory; defaults to config media_root")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--service-url")
    parser.add_argument("--model")
    parser.add_argument("--grace-hours", type=float, default=GRACE_HOURS_DEFAULT)
    parser.add_argument(
        "--max-files", type=int, default=1, help="Maximum transcriptions per run; 0 means unlimited"
    )
    parser.add_argument("--scan-only", action="store_true")
    parser.add_argument("--ignore-grace", action="store_true")
    parser.add_argument(
        "--rescan", action="store_true", help="Ignore cached classification and inspect files again"
    )
    parser.add_argument(
        "--clear-state", action="store_true", help="Delete the idempotence database before starting"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview only (the default)")
    mode.add_argument(
        "--apply", action="store_true", help="Allow subtitle generation and state changes"
    )
    parser.add_argument("--ffmpeg-timeout-minutes", type=float, default=FFMPEG_TIMEOUT_SECONDS / 60)
    parser.add_argument(
        "--whisper-timeout-minutes", type=float, default=WHISPER_TIMEOUT_SECONDS / 60
    )
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        parser.print_help()
        return 0
    args = parser.parse_args(arguments)
    args.dry_run = not args.apply
    if args.clear_state and not args.apply:
        parser.error("--clear-state requires --apply")
    if args.scan_only and args.apply:
        parser.error("--scan-only cannot be combined with --apply")
    if args.max_files < 0 or args.grace_hours < 0:
        parser.error("File limit and grace hours must be nonnegative")
    if args.ffmpeg_timeout_minutes <= 0 or args.whisper_timeout_minutes <= 0:
        parser.error("Timeouts must be positive")

    global STATE_DIR, STATE_DB, ASR_BASE_URL, ASR_MODEL, FFMPEG, FFPROBE, CURL
    try:
        config = load_config(args.config)
        whisper = config.get("whisper", {})
        selected = args.path or config.get("media_root")
        if not selected:
            parser.error("Supply --path or set media_root in config")
        STATE_DIR = args.state_dir or state_path(whisper, "WhisperSubtitles")
        STATE_DIR = STATE_DIR.expanduser()
        STATE_DB = STATE_DIR / "state.db"
        ASR_BASE_URL = (
            args.service_url
            or os.getenv("WHISPER_ASR_URL")
            or whisper.get("service_url")
            or "http://127.0.0.1:9001"
        ).rstrip("/")
        ASR_MODEL = args.model or whisper.get("model", "large-v3-turbo")
        FFMPEG, FFPROBE, CURL = (tool(config, name) for name in ("ffmpeg", "ffprobe", "curl"))
    except ValueError as e:
        parser.error(str(e))
    root = Path(selected).expanduser()
    if not root.exists():
        log(f"Media path unavailable: {root}")
        return 0
    try:
        if args.apply:
            check_asr_service()
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        log(str(e))
        return 1
    if args.clear_state and STATE_DB.exists():
        STATE_DB.unlink()
        log(f"Cleared state database: {STATE_DB}")

    if args.dry_run:
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE files (path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER,
            first_seen REAL, last_checked REAL, status TEXT, subtitle_path TEXT, error TEXT, failure_time REAL)""")
    else:
        db = connect_db()
    transcribed = 0
    examined = 0
    try:
        for video in iter_videos(root):
            examined += 1
            if process_file(db, video, args):
                transcribed += 1
                if args.max_files and transcribed >= args.max_files:
                    log(f"Reached --max-files {args.max_files}")
                    break
    finally:
        db.close()

    log(f"DONE: considered {examined} files; transcribed {transcribed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
