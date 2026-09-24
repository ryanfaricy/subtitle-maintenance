import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subtitle_maintenance.common import digest, fingerprint
from subtitle_maintenance.housekeeping import inspect, quarantine, restore


class HousekeepingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.video = self.root / "Episode.mkv"
        self.video.write_bytes(b"video")
        self.sub = self.root / "Episode.en.srt"
        self.sub.write_text("English subtitle")
        self.inv = dict(text=[dict(index=2)], bitmap=[], data=dict(streams=[]))

    def test_preview_never_changes_files(self):
        with patch("subtitle_maintenance.media.inventory", return_value=self.inv):
            result = inspect(self.video, "cleanup", False, self.root / "state")
        self.assertEqual(result["status"], "WOULD_QUARANTINE")
        self.assertTrue(self.sub.exists())
        self.assertFalse((self.root / "state").exists())

    def test_quarantine_restore_and_collision(self):
        receipt = quarantine(self.sub, self.video, fingerprint(self.video), self.root / "state")
        self.assertFalse(self.sub.exists())
        restore(receipt, False)
        self.assertFalse(self.sub.exists())
        restore(receipt, True)
        self.assertEqual(self.sub.read_text(), "English subtitle")
        with self.assertRaises(ValueError):
            restore(receipt, True)

    def test_changed_video_refused(self):
        fp = fingerprint(self.video)
        self.video.write_bytes(b"changed video")
        with self.assertRaises(ValueError):
            quarantine(self.sub, self.video, fp, self.root / "state")
        self.assertTrue(self.sub.exists())

    def test_bitmap_alone_does_not_allow_cleanup(self):
        self.inv["text"] = []
        self.inv["bitmap"] = [dict(index=2)]
        with patch("subtitle_maintenance.media.inventory", return_value=self.inv):
            self.assertEqual(
                inspect(self.video, "cleanup", True, self.root / "state")["status"],
                "NO_SAFE_CLEANUP",
            )
        self.assertTrue(self.sub.exists())

    def test_shared_stem_refused(self):
        (self.root / "Episode.mp4").write_bytes(b"other cut")
        with patch("subtitle_maintenance.media.inventory", return_value=self.inv):
            self.assertEqual(
                inspect(self.video, "cleanup", True, self.root / "state")["status"],
                "NO_SAFE_CLEANUP",
            )

    def test_human_approved_preserved(self):
        with patch("subtitle_maintenance.media.inventory", return_value=self.inv):
            result = inspect(
                self.video,
                "cleanup",
                True,
                self.root / "state",
                {"human_approved_sha256": [digest(self.sub)]},
            )
        self.assertEqual(result["status"], "NO_SAFE_CLEANUP")
        self.assertTrue(self.sub.exists())

    def test_unknown_and_forced_sidecars_preserved(self):
        self.sub.rename(self.root / "Episode.srt")
        (self.root / "Episode.en.forced.srt").write_text("forced")
        with patch("subtitle_maintenance.media.inventory", return_value=self.inv):
            self.assertEqual(
                inspect(self.video, "cleanup", True, self.root / "state")["status"],
                "NO_SAFE_CLEANUP",
            )
