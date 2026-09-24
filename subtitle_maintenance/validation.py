"""Direct phrase/display-interval checks; no inferred cue-start timestamps."""

import statistics
from collections import defaultdict


def phrase_evidence(cues, words, size=4):
    index = defaultdict(list)
    for i in range(len(words) - size + 1):
        index[tuple(w[0] for w in words[i : i + size])].append(i)
    evidence = []
    for n, c in enumerate(cues):
        matches = []
        for j in range(len(c["tokens"]) - size + 1):
            phrase = tuple(c["tokens"][j : j + size])
            positions = index.get(phrase, [])
            if len(positions) != 1:
                continue
            i = positions[0]
            first, last = words[i][1], words[i + size - 1][1]
            # A phrase belongs on screen, not necessarily at the cue onset.
            error = max(c["start"] - first, last - c["end"], 0)
            matches.append((error, first, last, " ".join(phrase)))
        if matches:
            # One vote per cue; do not overweight long multi-phrase captions.
            matches.sort()
            error, first, last, phrase = matches[len(matches) // 2]
            evidence.append(
                dict(
                    cue=n,
                    start=c["start"],
                    end=c["end"],
                    error=error,
                    audio_start=first,
                    audio_end=last,
                    phrase=phrase,
                )
            )
    return evidence


def interval_check(cues, words):
    evidence = phrase_evidence(cues, words)
    good = sum(x["error"] <= 0.75 for x in evidence)
    return dict(
        matched_cues=len(evidence),
        good=good,
        ratio=good / len(evidence) if evidence else 0,
        median_error=statistics.median(x["error"] for x in evidence) if evidence else None,
        passed=len(evidence) >= 3 and good / len(evidence) >= 0.8,
        evidence=evidence,
    )


def full_check(cues, words, duration):
    evidence = phrase_evidence(cues, words)
    bins = []
    for i in range(10):
        items = [x for x in evidence if duration * i / 10 <= x["start"] < duration * (i + 1) / 10]
        ratio = sum(x["error"] <= 0.75 for x in items) / len(items) if items else 0
        bins.append(
            dict(bin=i, matched=len(items), ratio=ratio, passed=len(items) >= 5 and ratio >= 0.8)
        )
    span = max((x["start"] for x in evidence), default=0) - min(
        (x["start"] for x in evidence), default=0
    )
    dialogue_cues = sum(not c.get("non_dialogue_drawing", False) for c in cues)
    coverage = len(evidence) / dialogue_cues if dialogue_cues else 0
    valid = all(
        0 <= c["start"] < c["end"] <= duration + 2 and c["end"] - c["start"] <= 15 for c in cues
    )
    passed = (
        valid
        and len(evidence) >= 100
        and coverage >= 0.6
        and span >= duration * 0.8
        and all(x["passed"] for x in bins)
    )
    return dict(
        passed=passed,
        valid_intervals=valid,
        matched=len(evidence),
        cue_coverage=coverage,
        span=span,
        bins=bins,
    )
