"""Opt-in acceptance of UNCHANGED local subtitles, never an installation gate.

Scores describe agreement with an imperfect ASR transcript, not probability of
correctness. Ordered exact word matching includes short cues in its denominator.
Unlike repair, this policy tolerates small timing errors solely to avoid writes.
"""

from difflib import SequenceMatcher

from .validation import phrase_evidence

POLICY_VERSION = 2
TIMING_TOLERANCE = 1.5


def assess(cues, words, duration):
    indexed = [(token, i) for i, cue in enumerate(cues) for token in cue["tokens"]]
    subtitle_words = [token for token, _ in indexed]
    audio_words = [word for word, _ in words]
    base = dict(policy_version=POLICY_VERSION, timing_tolerance_seconds=TIMING_TOLERANCE)
    if not indexed or not words or duration <= 0 or max(len(indexed), len(words)) > 30000:
        return dict(
            base, passed=False, reason="Missing or excessive evidence for bounded comparison"
        )
    if not all(
        0 <= c["start"] < c["end"] <= duration + 2 and c["end"] - c["start"] <= 15 for c in cues
    ):
        return dict(base, passed=False, reason="Invalid cue intervals")
    if any(a["start"] > b["start"] for a, b in zip(cues, cues[1:])):
        return dict(base, passed=False, reason="Cue order is not chronological")
    if any(a[1] > b[1] for a, b in zip(words, words[1:])):
        return dict(base, passed=False, reason="Transcript order is not chronological")
    # Exact normalized words, in order: punctuation/case are normalized by the
    # existing parser. Do not equate this score to semantic/text accuracy.
    pairs = []
    for block in SequenceMatcher(
        None, subtitle_words, audio_words, autojunk=False
    ).get_matching_blocks():
        for n in range(block.size):
            si, ai = block.a + n, block.b + n
            cue = cues[indexed[si][1]]
            error = max(cue["start"] - words[ai][1], words[ai][1] - cue["end"], 0)
            pairs.append((si, ai, error <= TIMING_TOLERANCE))
    matched = len(pairs)
    dialogue = matched / len(indexed)
    audio_coverage = matched / len(words)
    timing = sum(p[2] for p in pairs) / matched if matched else 0
    regions = []
    for i in range(10):
        lo, hi = duration * i / 10, duration * (i + 1) / 10
        eligible = {j for j, (_, ci) in enumerate(indexed) if lo <= cues[ci]["start"] < hi}
        local = [p for p in pairs if p[0] in eligible]
        agreement = len(local) / len(eligible) if eligible else 0
        aligned = sum(p[2] for p in local) / len(local) if local else 0
        regions.append(
            dict(
                region=i,
                matched_words=len(local),
                dialogue_agreement=agreement,
                timing_agreement=aligned,
                passed=len(local) >= 20 and agreement >= 0.9 and aligned >= 0.9,
            )
        )
    anchors = phrase_evidence(cues, words)
    span = max((e["audio_end"] for e in anchors), default=0) - min(
        (e["audio_start"] for e in anchors), default=0
    )
    passed = (
        dialogue >= 0.95
        and timing >= 0.95
        and audio_coverage >= 0.9
        and matched >= 200
        and len(anchors) >= 100
        and span >= duration * 0.8
        and all(r["passed"] for r in regions)
    )
    return dict(
        base,
        passed=passed,
        dialogue_agreement=dialogue,
        timing_agreement=timing,
        audio_coverage=audio_coverage,
        matched_words=matched,
        subtitle_words=len(indexed),
        audio_words=len(words),
        phrase_anchors=len(anchors),
        anchor_span=span,
        regions=regions,
    )
