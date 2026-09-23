import unittest
from subtitle_maintenance.episode_mapping import resolve,MappingReview,merge_candidates,release_title_match

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

    def test_filter_rank_deduplicate(self):
        titles=['Morning Mimosa','Stan-Dan Deliver']
        groups=[('library',[dict(file_id=1,release='American.Dad.S12E08.HDTV'),
                            dict(file_id=2,release='American Dad S12E08 Morning Mimosa.WEB')]),
                ('mapped',[dict(file_id=1,release='American.Dad.S12E08.HDTV'),
                           dict(file_id=3,release='American Dad S11E08 Stan-Dan Deliver.DVDRip.HI')])]
        kept,rejected=merge_candidates(groups,'Morning Mimosa',titles)
        self.assertEqual([e['file_id'] for e in kept],[2,1])
        self.assertEqual(kept[1]['search_numberings'],['library','mapped'])
        self.assertEqual([e['file_id'] for e in rejected],[3])

    def test_unknown_release_not_rejected(self):
        for release in ['American.Dad.S11E08.HDTV.x264-KILLERS','Unknown naming format','American Dad S11E08 Unknown Title.WEB']:
            self.assertEqual(release_title_match(release,'Morning Mimosa',['Stan-Dan Deliver']),'unknown')

    def test_longest_title_and_number_marker(self):
        self.assertEqual(release_title_match('American Dad 12x08 Morning Mimosa.WEB','Morning Mimosa',['Morning Mimosa']),'match')
        self.assertEqual(release_title_match('Show S01E01 Long Title Part II.WEB','Long Title Part I',['Long Title Part I','Long Title Part II']),'different')

    def test_short_exact_title_promoted_not_prefix(self):
        self.assertEqual(release_title_match('American.Dad.S12E02.CIAPOW.1080p.WEB-DL','CIAPOW',[]),'match')
        self.assertEqual(release_title_match('Show.S01E01.CIAPOW.WEB','CIA',[]),'unknown')
        self.assertEqual(release_title_match('Show.S01E01.CIAPOW.WEB','Other Title',['CIAPOW']),'unknown')

    def test_typo_only_promotes(self):
        title="Steve and Snot's Test-Tubular Adventure"
        release='American.Dad.S09E01.Steve.and.Snots.Testubular.Adventure.WEB-DL.x264.AAC'
        self.assertEqual(release_title_match(release,title,[]),'near_match')
        self.assertEqual(release_title_match('Show.S01E01.HDTV.x264-KILLERS',title,[]),'unknown')

    def test_ranking_tiers(self):
        title="Steve and Snot's Test-Tubular Adventure"
        names=['Show.S01E01.HDTV','Show.S01E01.Steve.and.Snots.Testubular.Adventure.WEB-DL','Show.S01E01.Steve.and.Snots.Test.Tubular.Adventure.WEB-DL']
        kept,rejected=merge_candidates([('library',[dict(file_id=i,release=n) for i,n in enumerate(names)])],title,[])
        self.assertEqual([e['file_id'] for e in kept],[2,1,0])
        self.assertEqual(rejected,[])
