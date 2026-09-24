import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from subtitle_maintenance import preservation, workflow


def fixture():
    cues, words = [], []
    for i in range(300):
        tokens = [str(i), "hello", "world", "again"]
        start = i * 4 + 1
        cues.append(dict(start=start, end=start + 1, tokens=tokens))
        words.extend((t, start + 0.1 + j * 0.2) for j, t in enumerate(tokens))
    return cues, words


class PreservationTests(unittest.TestCase):
    def test_good_and_wrong_dialogue(self):
        cues, words = fixture()
        self.assertTrue(preservation.assess(cues, words, 1200)["passed"])
        for cue in cues[::5]:
            cue["tokens"] = ["unrelated", "incorrect", "other", "speech"]
        self.assertFalse(preservation.assess(cues, words, 1200)["passed"])

    def test_threshold_and_concentrated_errors(self):
        cues, words = fixture()
        for cue in cues[::20]:
            cue["start"] += 3
            cue["end"] += 3
        result = preservation.assess(cues, words, 1200)
        self.assertAlmostEqual(result["timing_agreement"], 0.95)
        self.assertTrue(result["passed"])
        cues[10]["start"] += 3
        cues[10]["end"] += 3
        self.assertFalse(preservation.assess(cues, words, 1200)["passed"])
        cues, words = fixture()
        for cue in cues[30:45]:
            cue["start"] += 3
            cue["end"] += 3
        result = preservation.assess(cues, words, 1200)
        self.assertAlmostEqual(result["timing_agreement"], 0.95)
        self.assertFalse(result["passed"])

    def test_short_cues_count_and_incomplete_or_sparse_rejected(self):
        cues, words = fixture()
        for cue in cues[::3]:
            cue["tokens"] = ["wrong"]
        self.assertLess(preservation.assess(cues, words, 1200)["dialogue_agreement"], 0.95)
        cues, words = fixture()
        self.assertFalse(preservation.assess(cues[:100], words, 1200)["passed"])
        self.assertFalse(preservation.assess(cues[:10], words[:40], 1200)["passed"])
        self.assertFalse(preservation.assess(cues, list(reversed(words)), 1200)["passed"])

    def test_preserves_before_repair_and_provider_in_preview_and_apply(self):
        for apply in [False, True]:
            with self.subTest(apply=apply), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                video = root / "v.mp4"
                video.write_bytes(b"video")
                sub = root / "v.en.srt"
                sub.write_text("original")
                info = dict(
                    text=[],
                    bitmap=[],
                    audio_index=1,
                    duration=1200,
                    data={
                        "streams": [
                            {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2}
                        ]
                    },
                )
                args = SimpleNamespace(
                    audio_stream=None,
                    scan_only=False,
                    apply=apply,
                    no_download=False,
                    identity=None,
                )
                provider = Mock()
                cues, words = fixture()
                with (
                    patch.object(workflow.media, "inventory", return_value=info),
                    patch.object(workflow.media, "sidecars", return_value=([sub], [])),
                    patch.object(workflow.native, "transcript", return_value={}),
                    patch.object(workflow.native, "words", return_value=words),
                    patch.object(workflow.subtitles, "read", return_value=cues),
                    patch.object(workflow, "prepare") as prepare,
                    patch.object(workflow, "install") as install,
                ):
                    result = workflow.process(
                        video,
                        args,
                        {"keep_existing_95": True, "min_age_minutes": 0},
                        root,
                        provider,
                    )
                self.assertEqual(result["status"], "KEPT_EXISTING")
                prepare.assert_not_called()
                install.assert_not_called()
                self.assertEqual(provider.mock_calls, [])
                self.assertEqual(sub.read_text(), "original")
                self.assertEqual(video.read_bytes(), b"video")

    def test_review_labels_and_disabled_policy(self):
        for existing in [False, True]:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                video = root / "v.mp4"
                video.write_bytes(b"video")
                sub = root / "v.en.srt"
                if existing:
                    sub.write_text("original")
                info = dict(
                    text=[],
                    bitmap=[],
                    audio_index=1,
                    duration=1200,
                    data={
                        "streams": [
                            {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2}
                        ]
                    },
                )
                args = SimpleNamespace(
                    audio_stream=None,
                    scan_only=False,
                    apply=False,
                    no_download=False,
                    identity=None,
                )
                provider = Mock()
                provider.candidates.return_value = []
                with (
                    patch.object(workflow.media, "inventory", return_value=info),
                    patch.object(
                        workflow.media, "sidecars", return_value=([sub] if existing else [], [])
                    ),
                    patch.object(workflow, "prepare", return_value=(None, {"passed": False})),
                    patch.object(workflow, "identity", return_value={}),
                    patch.object(workflow.native, "transcript") as transcript,
                ):
                    result = workflow.process(video, args, {"min_age_minutes": 0}, root, provider)
                transcript.assert_not_called()
                self.assertEqual(
                    result["status"],
                    "EXISTING_SUBTITLES_REVIEW" if existing else "MISSING_SUBTITLES",
                )
