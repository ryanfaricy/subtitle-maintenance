import unittest

from subtitle_maintenance.media import choose_audio, single_untagged_audio


class AudioSelectionTests(unittest.TestCase):
    def track(self, index, channels=2, default=0, language="eng", title="", **flags):
        return dict(
            index=index,
            channels=channels,
            codec_type="audio",
            tags=dict(language=language, title=title),
            disposition=dict(default=default, **flags),
        )

    def test_single_default_beats_cheaper_track(self):
        self.assertEqual(choose_audio([self.track(1), self.track(2, 6, 1)])["index"], 2)

    def test_duplicate_defaults_prefer_stereo(self):
        self.assertEqual(choose_audio([self.track(2, 6, 1), self.track(1, 2, 1)])["index"], 1)

    def test_no_default_and_ties(self):
        self.assertEqual(choose_audio([self.track(3), self.track(2), self.track(1, 6)])["index"], 2)

    def test_missing_channel_count_last(self):
        self.assertEqual(choose_audio([self.track(1, None), self.track(2)])["index"], 2)

    def test_non_dialogue_and_non_english_excluded(self):
        for bad in [
            self.track(1, default=1, title="Commentary"),
            self.track(1, default=1, comment=1),
            self.track(1, default=1, visual_impaired=1),
            self.track(1, default=1, descriptions=1),
            self.track(1, default=1, language="fra"),
            self.track(1, language="und"),
        ]:
            self.assertEqual(choose_audio([bad, self.track(2)])["index"], 2)
            self.assertIsNone(choose_audio([bad]))

    def test_untagged_commentary_flag_refused(self):
        self.assertIsNone(
            single_untagged_audio({"streams": [self.track(1, language="und", comment=1)]})
        )
