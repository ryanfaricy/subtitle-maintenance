import html
import re
import statistics
from pathlib import Path

from .validation import full_check, interval_check, phrase_evidence

STAMP = r"\d+:\d{2}:\d{2}[,.]\d{1,3}"
TIMING = re.compile(r"^(" + STAMP + r")\s*-->\s*(" + STAMP + r")[^\n]*$", re.M)
DIALOGUE_SCORING_VERSION = 2


def drawing_path(text):
    """Recognize a complete vector path, never a substring of spoken text."""
    parts = text.strip().split()
    if not parts or parts[0] != "m":
        return False
    commands = []
    for part in parts:
        if part in {"m", "n", "l", "b", "s", "p", "c"}:
            commands.append([part, 0])
        elif commands and re.fullmatch(r"-?\d+(?:\.\d+)?", part):
            commands[-1][1] += 1
        else:
            return False
    if len(commands) < 2 or sum(count for _, count in commands) < 6:
        return False
    return all(
        count == 0
        if command == "c"
        else count >= 6 and count % 6 == 0
        if command == "b"
        else count >= 6 and count % 2 == 0
        if command == "s"
        else count >= 2 and count % 2 == 0
        for command, count in commands
    )


def dialogue_text(text):
    """Exclude ASS drawing spans for scoring; retain original cue text elsewhere.

    Explicit \\pN drawing mode ends at \\p0 or a style reset. Some SRT exports
    lose that flag but retain positioning tags and complete vector paths: only
    those strictly recognized spans are excluded. Ambiguous text remains text.
    """
    text = html.unescape(text)
    positioned = bool(re.search(r"\\(?:pos|move)\s*\(|\\an[1-9]\b", text))
    drawing = False
    removed = False
    spoken = []
    for part in re.split(r"(\{[^}]*\})", text):
        if part.startswith("{") and part.endswith("}"):
            for tag in re.finditer(r"\\p(\d+)(?![\d\w])|\\r(?:[^\\}]*)", part):
                drawing = bool(int(tag[1])) if tag[1] is not None else False
            continue
        if part.strip() and (drawing or (positioned and drawing_path(part))):
            removed = True
        else:
            spoken.append(part)
    return re.sub(r"<[^>]*>", " ", " ".join(spoken)), removed


def seconds(value):
    h, m, s = value.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def tokens(text):
    text, _ = dialogue_text(text)
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", text.lower().replace("’", "'"))


def read(path):
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(TIMING.finditer(text))
    cues = []
    for i, m in enumerate(matches):
        body = text[m.end() : matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
        # Remove next numeric sequence identifier, even without a blank line.
        body = re.sub(r"\n\s*\d+\s*$", "", body).strip()
        if not body:
            raise ValueError("Empty cue")
        cue_tokens = tokens(body)
        _, removed_drawing = dialogue_text(body)
        cues.append(
            dict(
                start=seconds(m[1]),
                end=seconds(m[2]),
                text=body,
                tokens=cue_tokens,
                non_dialogue_drawing=removed_drawing and not cue_tokens,
            )
        )
    if not cues:
        raise ValueError("No readable SRT cues")
    return cues


def stamp(value):
    if value < 0:
        raise ValueError("Negative subtitle timestamp")
    ms = round(value * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def shifted(source, destination, offset, scale=1.0):
    cues = read(source)
    result = (
        "\n\n".join(
            f"{i}\n{stamp(c['start'] * scale + offset)} --> {stamp(c['end'] * scale + offset)}\n{c['text']}"
            for i, c in enumerate(cues, 1)
        )
        + "\n"
    )
    Path(destination).write_text(result, encoding="utf-8")
    if [c["text"] for c in read(destination)] != [c["text"] for c in cues]:
        raise ValueError("Cue text changed")


def validate(cues, words, duration, samples, max_shift=60, required_samples=3):
    full = full_check(cues, words, duration)
    offset = 0.0
    if not full["passed"]:
        evidence = phrase_evidence(cues, words)
        if len(evidence) >= 100:
            offset = statistics.median(
                (e["audio_start"] - e["start"] + e["audio_end"] - e["end"]) / 2 for e in evidence
            )
        if abs(offset) > max_shift:
            return dict(
                passed=False, reason="offset exceeds allowed correction", offset=offset, full=full
            )
        cues = [dict(c, start=c["start"] + offset, end=c["end"] + offset) for c in cues]
        full = full_check(cues, words, duration)
    if not full["passed"]:
        return dict(
            passed=False,
            reason="wrong cut, sparse evidence, drift, or timing mismatch",
            offset=offset,
            full=full,
        )
    checks = []
    for start, words in samples():
        selected = [c for c in cues if start + 3 <= c["start"] < start + 34]
        checks.append(dict(start=start, **interval_check(selected, words)))
    return dict(
        passed=len(checks) == required_samples and all(c["passed"] for c in checks),
        offset=offset,
        full=full,
        samples=checks,
    )
