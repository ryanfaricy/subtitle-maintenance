import tempfile
import unittest
from pathlib import Path

from subtitle_maintenance.subtitles import read, shifted, tokens
from subtitle_maintenance.validation import full_check


class DrawingTextTests(unittest.TestCase):
    def test_explicit_drawing_and_mixed_dialogue(self):
        self.assertEqual(tokens(r"{\p1}m 0 0 l 10 0 10 10{\p0}Hello there!"), ["hello", "there"])
        self.assertEqual(tokens(r"Before {\p2}m 0 0 l 10 10{\r}after"), ["before", "after"])
        self.assertEqual(tokens(r"{\p1}m 0 0 l 10 10"), [])
        self.assertEqual(tokens(r"{\pos(1,2)\pbo4}I have 12 apples"), ["i", "have", "12", "apples"])

    def test_exported_positioned_paths_not_normal_numbers(self):
        self.assertEqual(tokens(r"{\an2}{\pos(86,258)}m 325 0 l 355 0 l 332 47"), [])
        self.assertEqual(tokens(r"{\an2}m 0 0 b 1 2 3 4 5 6 c"), [])
        self.assertEqual(
            tokens("m 325 0 l 355 0 l 332 47"), ["m", "325", "0", "l", "355", "0", "l", "332", "47"]
        )
        self.assertEqual(
            tokens(r"{\an2}Meet me at 10 or 11"), ["meet", "me", "at", "10", "or", "11"]
        )
        self.assertIn("hello", tokens(r"{\an2}m 0 0 l 1 2 3 4 hello"))
        self.assertIn("m", tokens(r"{\an2}m 0 0 b 1 2 3 4"))

    def test_serialization_preserves_drawings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.srt"
            out = Path(folder) / "out.srt"
            body = r"{\an2}{\pos(86,258)}m 325 0 l 355 0 l 332 47"
            path.write_text("1\n00:00:02,000 --> 00:00:05,000\n" + body + "\n")
            cue = read(path)[0]
            self.assertTrue(cue["non_dialogue_drawing"])
            self.assertEqual(cue["tokens"], [])
            shifted(path, out, 0.1)
            self.assertEqual(read(out)[0]["text"], body)

    def test_only_confirmed_drawings_excluded_from_coverage(self):
        cues = []
        words = []
        for i in range(120):
            words_here = [str(i), "hello", "world", "again"]
            cues.append(dict(start=i * 10, end=i * 10 + 5, tokens=words_here))
            words.extend((t, i * 10 + 1 + j * 0.5) for j, t in enumerate(words_here))
        drawings = [dict(start=1, end=3, tokens=[], non_dialogue_drawing=True) for _ in range(120)]
        result = full_check(cues + drawings, words, 1200)
        self.assertTrue(result["passed"])
        self.assertEqual(result["cue_coverage"], 1)
        for cue in drawings:
            cue["non_dialogue_drawing"] = False
        self.assertFalse(full_check(cues + drawings, words, 1200)["passed"])
