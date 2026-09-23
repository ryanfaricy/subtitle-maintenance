import unittest
from subtitle_maintenance.episode_mapping import resolve,MappingReview

class EpisodeMappingTests(unittest.TestCase):
    def setUp(self):
        self.identity=dict(imdb='tt0397306',season=10,episode=1,title="Steve and Snot's Test-Tubular Adventure")
        self.catalog=dict(imdb='tt0397306',show_id=215,episodes=[dict(id=1,season=9,number=1,name='Steve & Snot’s Test-Tubular Adventure')])
    def test_unique_title_alternate_number(self):
        mapped,evidence=resolve(self.identity,self.catalog)
        self.assertEqual((mapped['season'],mapped['episode']),(9,1))
        self.assertEqual(self.identity['season'],10)
        self.assertEqual(evidence['original_season'],10)
    def test_duplicate_and_missing_fail_closed(self):
        self.catalog['episodes']*=2
        with self.assertRaises(MappingReview):resolve(self.identity,self.catalog)
        self.catalog['episodes']=[]
        with self.assertRaises(MappingReview):resolve(self.identity,self.catalog)
    def test_wrong_show_and_unnumbered_fail(self):
        self.catalog['imdb']='tt0000'
        with self.assertRaises(MappingReview):resolve(self.identity,self.catalog)
        self.catalog['imdb']=self.identity['imdb'];self.catalog['episodes'][0]['number']=None
        with self.assertRaises(MappingReview):resolve(self.identity,self.catalog)
