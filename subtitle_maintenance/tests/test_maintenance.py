import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from subtitle_maintenance import common,media,subtitles,workflow
from subtitle_maintenance.validation import full_check,interval_check

class MaintenanceTests(unittest.TestCase):
    def test_sidecars_exclude_backups_forced_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);v=d/'Video.mkv';v.touch()
            for name in ['Video.en.srt','Video.en.forced.srt','Video.en.old.srt','Video.srt','Video.english.ass','Video.fr.srt','Video.en.srt.backup']:(d/name).touch()
            english,unknown=media.sidecars(v)
            self.assertEqual([p.name for p in english],['Video.en.srt','Video.english.ass'])
            self.assertEqual([p.name for p in unknown],['Video.srt'])
    def test_unknown_forced_not_trusted(self):
        self.assertFalse(media.english_stream({'tags':{'language':'und','title':'English?'}}))
        self.assertTrue(media.forced({'tags':{'title':'English Forced'}}))
    def test_srt_render_preserves_text(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'source.srt';out=Path(d)/'out.srt'
            source.write_text('1\n00:00:01,000 --> 00:00:03,000\nHello, <i>world!</i>\n\n2\n00:00:04,000 --> 00:00:05,000\nGoodbye.\n')
            subtitles.shifted(source,out,1.5)
            self.assertEqual(subtitles.read(out)[0]['start'],2.5)
            self.assertEqual([c['text'] for c in subtitles.read(source)],[c['text'] for c in subtitles.read(out)])
    def fixture(self):
        cues=[];words=[]
        for i in range(120):
            tokens=[str(i),'hello','world','again']
            cues.append(dict(start=i*10,end=i*10+5,tokens=tokens))
            words.extend((t,i*10+1.5+j*.5) for j,t in enumerate(tokens))
        return cues,words
    def test_natural_lead_passes_and_wrong_cut_fails(self):
        cues,words=self.fixture()
        self.assertTrue(full_check(cues,words,1200)['passed'])
        self.assertFalse(full_check(cues,[(w,t+40 if t>600 else t) for w,t in words],1200)['passed'])
    def test_wrong_offset_and_sparse_fail(self):
        cues,words=self.fixture()
        self.assertFalse(full_check(cues,[(w,t+26) for w,t in words],1200)['passed'])
        self.assertFalse(full_check(cues[:10],words,1200)['passed'])
        self.assertFalse(interval_check([],[])['passed'])
    def test_backup_install_and_restore(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);video=d/'v.mkv';video.write_bytes(b'video')
            target=d/'v.en.srt';target.write_bytes(b'old');source=d/'candidate';source.write_bytes(b'new')
            receipt=common.install(source,target,d/'backups',common.digest(target),video,common.fingerprint(video))
            self.assertEqual(target.read_bytes(),b'new');self.assertEqual(Path(receipt['backup']).read_bytes(),b'old')
            common.install(Path(receipt['backup']),target,d/'rollback',receipt['installed_sha256'],video,common.fingerprint(video))
            self.assertEqual(target.read_bytes(),b'old')
    def test_changed_target_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);v=d/'v';v.write_text('v');t=d/'t';t.write_text('old');s=d/'s';s.write_text('new')
            with self.assertRaises(ValueError):common.install(s,t,d/'backup','wrong',v,common.fingerprint(v))
            self.assertEqual(t.read_text(),'old')
    def test_ocr_preview_does_not_launch(self):
        with patch.object(workflow,'run') as run:
            self.assertEqual(workflow.ocr(Path('v.mkv'),{}, {},Path('.'),False)['status'],'WOULD_OCR')
            run.assert_not_called()
    def test_scan_trusts_text_without_provider(self):
        with tempfile.TemporaryDirectory() as d:
            v=Path(d)/'v.mkv';v.touch();provider=SimpleNamespace()
            info=dict(text=[{}],bitmap=[],audio_index=1)
            with patch.object(media,'inventory',return_value=info):
                result=workflow.process(v,SimpleNamespace(audio_stream=None,scan_only=True),{'min_age_minutes':0},Path(d),provider)
            self.assertEqual(result['status'],'TRUSTED_EMBEDDED')
    def test_new_sidecar_does_not_overwrite_unexpected_file(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);v=d/'v';v.touch();target=d/'t';target.write_text('user');source=d/'s';source.write_text('new')
            with self.assertRaises(ValueError):common.install(source,target,d/'b',None,v,common.fingerprint(v))

if __name__=='__main__':unittest.main()
