"""Native word timestamps, serial GPU access, bounded child processes."""

import fcntl
import hashlib
import json
import tempfile
import time
import urllib.request
from pathlib import Path

from .common import atomic_json, fingerprint, run
from .subtitles import tokens


def words(data, offset=0):
    result = []
    for s in data.get("segments", []):
        for w in s.get("words", []):
            result.extend((t, float(w["start"]) + offset) for t in tokens(w.get("word", "")))
    return result


def transcript(video, info, start, length, config, state):
    model = config["model"]
    audio = info["audio_index"]
    if audio is None:
        raise ValueError("English audio is unknown or ambiguous; select an audio stream explicitly")
    fp = fingerprint(video)
    key = hashlib.sha256(f"{fp}:{audio}:{start}:{length}:{model}:native-v2".encode()).hexdigest()
    cache = state / "transcripts" / (key + ".json")
    if cache.exists():
        return json.loads(cache.read_text())
    # Reuse old caches only when the selected stream was the first audio stream.
    audio_streams = [s for s in info["data"]["streams"] if s.get("codec_type") == "audio"]
    legacy = config.get("legacy_transcripts")
    if legacy and audio_streams and audio == audio_streams[0]["index"]:
        st = video.stat()
        oldfp = hashlib.sha256(
            f"{video.resolve()}\0{st.st_size}\0{st.st_mtime_ns}".encode()
        ).hexdigest()
        oldkey = hashlib.sha256(
            f"{oldfp}:{start}:{length}:{model}:native-word-v1".encode()
        ).hexdigest()
        old = Path(legacy) / (oldkey + ".json")
        if old.exists():
            return json.loads(old.read_text())
    if config.get("cache_only"):
        raise ValueError("Native transcript not cached; rerun without --cache-only")
    if not config.get("python") or not model:
        raise ValueError("Whisper Python/model not configured")
    cache.parent.mkdir(parents=True, exist_ok=True)
    lockpath = Path(tempfile.gettempdir()) / "com.ryan.whisper-gpu.lock"

    def idle():
        url = config.get("service_url")
        if not url:
            return True
        with urllib.request.urlopen(url.rstrip("/") + "/queue-metrics", timeout=10) as response:
            q = json.load(response)["queue"]
        return not (q["requests_queued"] or q["requests_in_flight"])

    with lockpath.open("a") as lock:
        deadline = time.monotonic() + 300
        while True:
            if time.monotonic() > deadline:
                raise RuntimeError("Whisper busy; retry later")
            if idle():
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    if idle():
                        break
                    fcntl.flock(lock, fcntl.LOCK_UN)
                except BlockingIOError:
                    pass
            time.sleep(3)
        try:
            with tempfile.TemporaryDirectory(prefix="subtitle-audio-") as d:
                audiofile = Path(d) / "audio.wav"
                output = Path(d) / "words.json"
                cmd = ["ffmpeg", "-nostdin", "-v", "error", "-threads", "1", "-i", video]
                if start is not None:
                    cmd += ["-ss", str(start), "-t", str(length)]
                cmd += ["-map", f"0:{audio}", "-vn", "-ac", "1", "-ar", "16000", "-y", audiofile]
                print(
                    "    Transcribing " + ("full episode" if start is None else f"{start}s sample"),
                    flush=True,
                )
                run(cmd, 600)
                import wave

                with wave.open(str(audiofile)) as w:
                    actual = w.getnframes() / w.getframerate()
                expected = length if length else info["duration"]
                if actual < expected * 0.98:
                    raise ValueError("Incomplete decoded audio")
                worker = Path(__file__).with_name("native_worker.py")
                run([config["python"], worker, audiofile, output, model], 3600)
                data = json.loads(output.read_text())
                atomic_json(cache, data)
                if fingerprint(video) != fp:
                    raise ValueError("Video changed during transcription")
                return data
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
