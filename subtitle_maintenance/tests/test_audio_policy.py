import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from subtitle_maintenance.media import single_untagged_audio
from subtitle_maintenance.audio_tags import tag_missing
from subtitle_maintenance.providers import Provider, DownloadBudgetReached

class AudioPolicyTests(unittest.TestCase):
    def stream(self,language=None,title=''):
        return dict(index=1,codec_type='audio',tags=dict(title=title,**({'language':language} if language is not None else {})))
    def test_single_missing_only(self):
        for lang in [None,'','und']:
            self.assertEqual(single_untagged_audio({'streams':[self.stream(lang)]}),1)
        for lang in ['eng','fra','mul','zxx']:
            self.assertIsNone(single_untagged_audio({'streams':[self.stream(lang)]}))
        self.assertIsNone(single_untagged_audio({'streams':[self.stream(),self.stream()]}))
        self.assertIsNone(single_untagged_audio({'streams':[self.stream(title='Commentary')]}))
    def test_tag_preview_and_container_gate(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'v.mkv';p.write_bytes(b'original')
            with patch('subtitle_maintenance.media.inventory',return_value={'data':{'streams':[self.stream()]}}),patch('subtitle_maintenance.audio_tags.run') as run:
                self.assertEqual(tag_missing(p,False,Path(d),{'min_age_minutes':0})['status'],'WOULD_TAG_AUDIO_ENGLISH')
                p=p.rename(p.with_suffix('.avi'))
                self.assertEqual(tag_missing(p,True,Path(d),{})['status'],'REVIEW_CONTAINER')
                run.assert_not_called()
    def test_budget_is_typed_and_cache_exempt(self):
        with tempfile.TemporaryDirectory() as d:
            p=Provider.__new__(Provider);p.state=Path(d);p.config={'max_downloads':0};p.downloads=0
            with self.assertRaises(DownloadBudgetReached):p.download({'file_id':1})
            (Path(d)/'downloads').mkdir();(Path(d)/'downloads/1.srt').write_text('cached')
            self.assertEqual(p.download({'file_id':1}).read_text(),'cached')
