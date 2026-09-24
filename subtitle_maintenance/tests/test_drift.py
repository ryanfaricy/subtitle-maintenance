import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from subtitle_maintenance import drift, subtitles, workflow


class DriftTests(unittest.TestCase):
    def test_end_to_end_synthetic_dialogue_and_render(self):
        # Only audio acquisition is mocked: phrase matching, fit, all verification
        # gates, timestamp serialization and the second verification are real.
        words=[];blocks=[]
        for i,y in enumerate(range(10,1990,4),1):
            tokens=[str(i),'hello','world','again']
            words.extend((token,y-.6+j*.4) for j,token in enumerate(tokens))
            start=(y-1)/1.042-1;end=(y-1)/1.042+1
            blocks.append(f'{i}\n{subtitles.stamp(start)} --> {subtitles.stamp(end)}\n'+ ' '.join(tokens))
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.srt';source.write_text('\n\n'.join(blocks)+'\n')
            def transcript(video,info,start,length,config,state):
                return [w for w in words if start is None or start<=w[1]<start+length]
            with patch.object(workflow.native,'transcript',side_effect=transcript),patch.object(workflow.native,'words',side_effect=lambda data,*args:data):
                candidate,check=workflow.prepare(Path('S03E02.mkv'),source,{'duration':2000},{'allow_drift_correction':True},Path(folder),Path(folder))
            self.assertIsNotNone(candidate)
            self.assertTrue(check['passed'])
            self.assertAlmostEqual(check['scale'],1.042,places=5)
            self.assertEqual(len(check['rendered_verification']['samples']),5)
            self.assertEqual(subtitles.read(source)[0]['text'],subtitles.read(candidate)[0]['text'])

    def evidence(self, scale=1.042, offset=1):
        return [dict(start=(y-offset)/scale-1,end=(y-offset)/scale+1,
                     audio_start=y-1,audio_end=y+1) for y in range(10,1990,4)]

    def test_recovers_global_scale(self):
        with patch.object(drift,'phrase_evidence',return_value=self.evidence()):
            result=drift.fit([],[],2000)
        self.assertAlmostEqual(result['scale'],1.042)
        self.assertAlmostEqual(result['offset'],1)
        self.assertGreater(result['anchors'],100)

    def test_rejects_sparse_extreme_and_piecewise(self):
        broken=self.evidence()
        for e in broken:
            if e['audio_start']>1000:
                e['audio_start']+=12;e['audio_end']+=12
        for evidence in [self.evidence()[:20],self.evidence(1.1),self.evidence(offset=80),broken]:
            with self.subTest(evidence=evidence[:1]),patch.object(drift,'phrase_evidence',return_value=evidence):
                with self.assertRaises(ValueError):drift.fit([],[],2000)

    def test_combined_episode(self):
        for name in ['Show S03E08-E09','Show S03E08E09','Show S03E08-09']:
            self.assertTrue(drift.combined_episode(name))
        self.assertFalse(drift.combined_episode('Show S03E08 - Title'))

    def test_scaled_render_preserves_text(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.srt';out=Path(folder)/'out.srt'
            source.write_text('1\n00:00:10,000 --> 00:00:12,000\nHello there!\n')
            subtitles.shifted(source,out,1,1.04)
            cue=subtitles.read(out)[0]
            self.assertEqual((cue['start'],cue['end'],cue['text']),(11.4,13.48,'Hello there!'))

    def test_disabled_and_combined_do_not_fit(self):
        for name,config in [('S03E01',{}),('S03E08-E09',{'allow_drift_correction':True})]:
            with patch.object(subtitles,'read',return_value=[]),patch.object(workflow.native,'transcript'),patch.object(workflow.native,'words',return_value=[]),patch.object(subtitles,'validate',return_value={'passed':False,'full':{'passed':False}}),patch.object(drift,'fit') as fit:
                workflow.verify(Path(name),Path('test.srt'),{'duration':2000},config,Path('.'))
                fit.assert_not_called()

    def test_render_rechecks_five_samples_with_no_shift(self):
        def validate(cues,words,duration,samples,max_shift,required_samples):
            self.assertEqual(max_shift,0)
            self.assertEqual(required_samples,5)
            self.assertEqual(len(list(samples())),5)
            return {'passed':True,'offset':0}
        with patch.object(subtitles,'read',return_value=[]),patch.object(workflow.native,'transcript'),patch.object(workflow.native,'words',return_value=[]),patch.object(subtitles,'validate',side_effect=validate):
            workflow.verify(Path('S03E01'),Path('test.srt'),{'duration':2000},{},Path('.'),drift_render=True)

    def test_prepare_routes_scale_only_to_five_sample_recheck(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'test.srt';source.write_text('1\n00:00:10,000 --> 00:00:12,000\nHello there!\n')
            with patch.object(workflow,'verify',side_effect=[{'passed':True,'offset':0,'scale':1.04},{'passed':True,'offset':0}]) as verify:
                candidate,check=workflow.prepare(Path('v.mkv'),source,{}, {},Path(folder),Path(folder))
                self.assertNotEqual(candidate,source)
                self.assertTrue(verify.call_args.kwargs['drift_render'])
