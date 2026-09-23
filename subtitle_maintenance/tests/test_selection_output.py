import unittest
from subtitle_maintenance.cli import selection_lines

class SelectionOutputTests(unittest.TestCase):
    def test_winner_and_shift(self):
        data=dict(selected_provider=dict(provider='OpenSubtitles',release='QI XL release',
                  file_id=123,url='https://www.opensubtitles.com/en/subtitles/example'),
                  selected_offset_seconds=-1.25,destination='/example/episode.en.srt')
        lines=selection_lines(data)
        self.assertIn('QI XL release',lines[0])
        self.assertIn('123',lines[0])
        self.assertIn('-1.250s',lines[2])
        self.assertIn(data['destination'],lines[3])
        data['selected_offset_seconds']=0
        self.assertEqual(selection_lines(data)[2],'Timing: unchanged')
    def test_no_selection_no_output(self):
        self.assertEqual(selection_lines({'status':'UNRESOLVED'}),[])
